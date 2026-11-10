"""Notion 「연구 지시함」 행을 원장 과제로 바꾸는 규칙.

지피가 쓴 행은 지시일 뿐 승인이 아니다. 이 모듈은 판단이 필요 없는 규칙만 적용한다:
- 처리한 행은 다시 처리하지 않는다(inbox_seen).
- 승인 대상 행동은 실행하지 않고 사용자 판단 목록으로 보낸다.
- 승인된 연구 분야 밖의 새 목표는 거절하고 사유를 남긴다.
- 원장 상태를 직접 '완료'로 바꾸는 요청은 받지 않는다.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

from .ledger import Ledger
from .model import Stage, Status, Task

KINDS = {"new_goal", "new_task", "set_priority", "retry", "approval_request", "question"}
NEEDS_OWNER = re.compile(r"공개|투고|게시|외부\s*전송|발송|메일\s*보내|결제|구매|충전|유료|API\s*키|인증|권한|공유", re.I)
FORBIDDEN_STATE = re.compile(r"완료\s*처리|done\s*으로|통과로\s*표시|검토\s*통과|승인\s*처리", re.I)


@dataclasses.dataclass
class InboxRow:
    row_id: str
    kind: str
    content: str
    target: str = ""
    priority: int = 3


@dataclasses.dataclass
class Outcome:
    row_id: str
    result: str                 # accepted | rejected | needs_owner | answered | duplicate
    reason: str
    task_ids: list[str] = dataclasses.field(default_factory=list)


def process(rows: list[InboxRow], ledger: Ledger, seen: set[str], approved_scopes: list[str]) -> list[Outcome]:
    out: list[Outcome] = []
    for r in rows:
        if r.row_id in seen:
            out.append(Outcome(r.row_id, "duplicate", "이미 처리한 행"))
            continue
        seen.add(r.row_id)
        if r.kind not in KINDS:
            out.append(Outcome(r.row_id, "rejected", f"알 수 없는 종류: {r.kind}"))
            continue
        if FORBIDDEN_STATE.search(r.content):
            out.append(Outcome(r.row_id, "rejected", "원장 상태를 직접 완료·통과로 바꾸는 요청은 받지 않음(결과는 실행 기록으로만 생김)"))
            continue
        if r.kind == "approval_request" or NEEDS_OWNER.search(r.content):
            out.append(Outcome(r.row_id, "needs_owner", "공개·투고·외부 전송·비용·인증·권한 관련 — 사용자 직접 승인 필요"))
            continue
        if r.kind == "question":
            out.append(Outcome(r.row_id, "answered", "상태 요약(status/summary.json)으로 답함"))
            continue
        if r.kind == "new_goal":
            if not any(s in r.content for s in approved_scopes):
                out.append(Outcome(r.row_id, "needs_owner", "승인된 연구 분야 밖일 수 있음 — 사용자 확인 필요"))
                continue
            pid = f"G{r.row_id[:8]}"
            t = ledger.add(Task(id=f"{pid}:collect:v0", project=pid, stage=Stage.COLLECT,
                                checkpoint={"brief": r.content[:2000], "priority": r.priority, "from_inbox": r.row_id}))
            out.append(Outcome(r.row_id, "accepted", "새 목표 → 수집 과제 생성", [t.id]))
            continue
        if r.kind in ("set_priority", "retry", "new_task"):
            if r.target and r.target not in ledger.tasks and r.kind != "new_task":
                out.append(Outcome(r.row_id, "rejected", f"대상 과제 없음: {r.target}"))
                continue
            if r.kind == "set_priority":
                t = ledger.get(r.target)
                cp = {**t.checkpoint, "priority": r.priority}
                ledger.update(t.id, t.rev, checkpoint=cp)
                out.append(Outcome(r.row_id, "accepted", "우선순위 변경", [t.id]))
            elif r.kind == "retry":
                t = ledger.get(r.target)
                if t.status == Status.FAILED:
                    # 실패는 종결 상태이므로 되살리지 않고 새 시도 과제를 만든다(기록 보존).
                    nt = ledger.add(dataclasses.replace(t, id=f"{t.id}:retry{t.attempts}", status=Status.QUEUED,
                                                        attempts=0, rev=0, error_class=None, error_detail=""))
                    out.append(Outcome(r.row_id, "accepted", "실패 과제의 새 시도 생성(원 기록 보존)", [nt.id]))
                elif t.status == Status.BLOCKED:
                    out.append(Outcome(r.row_id, "needs_owner", "접근 제한·권한 문제는 재시도로 우회하지 않음 — 대체 출처 또는 사용자 판단"))
                else:
                    out.append(Outcome(r.row_id, "rejected", f"재시도 대상 아님(현재 {t.status.value})"))
            else:
                out.append(Outcome(r.row_id, "needs_owner", "과제 단위 추가는 프로젝트·선행조건 지정 필요 — 관리자 검토"))
    return out


def summary(ledger: Ledger, observed_at: str) -> dict[str, Any]:
    """지피용 비공개 요약: 원본 상태값 목록 + 합계 + 조회 시각(실제 시험에서 정한 규칙)."""
    rows = [{"task": t.id, "stage": t.stage.value, "status": t.status.value, "attempts": t.attempts,
             "run": t.lease_owner, "error_class": t.error_class.value if t.error_class else None,
             "error": t.error_detail[:200], "test": t.test} for t in sorted(ledger.all(), key=lambda x: x.id)]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"observed_at": observed_at, "total": len(rows), "counts": counts, "rows": rows}
