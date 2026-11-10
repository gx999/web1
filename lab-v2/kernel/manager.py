"""운영 관리자: 배정·임대·재시도·한도 대기·다음 단계 생성·독립 검토 연결.

관리자는 판단이 필요 없는 일(배정, 실패 분류, 다음 과제 생성)만 결정론적으로
처리한다. 연구 판단은 실행 세션(연구원)이 하고, 관리자는 그 결과의 저장 여부와
검토 조건만 확인한다.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from .ledger import ConflictError, Ledger
from .model import (
    PIPELINE, ErrorClass, Source, SourceStatus, Stage, Status, Task, content_hash,
)

LEASE_SECONDS = 45 * 60          # 한 실행의 임대 시간
BACKOFF = [60, 300, 1800]        # 일시 장애 재시도 간격(초)
LIMIT_RECHECK = 60 * 60          # 한도 재설정 시각을 모를 때의 재확인 간격
MAX_REVISION_ROUNDS = 3


class Executor(Protocol):
    """실행 세션을 띄우고 상태를 묻는 어댑터 (실제: create_session/get_session)."""

    def launch(self, task: Task) -> str: ...
    def status(self, run_id: str) -> str: ...  # running | idle | failed | unknown | gone


def classify(error: str) -> ErrorClass:
    """오류 문자열 분류.

    실제 시험(2026-11-10)에서 OpenAlex가 'Rate limit exceeded'(외부 출처의 IP 공유 한도)를
    돌려줬다. 이것은 우리 구독 사용량 한도가 아니므로 LIMIT로 분류하면 안 된다.
    구독 한도는 세션 메타데이터(rate_limit_info.status='limited') 또는 'usage limit'
    문구로만 판정하고, 외부 출처 한도는 일시 장애로 보고 늦춰 재시도한다.
    """
    e = error.lower()
    if e.startswith("source_rate_limit:") or ("rate limit exceeded" in e and "usage" not in e):
        return ErrorClass.TRANSIENT
    if e.startswith("usage_limit:") or any(k in e for k in ("usage limit", "limit reached", "rate_limit_info=limited")):
        return ErrorClass.LIMIT
    if e.startswith("access:") or any(k in e for k in ("403", "captcha", "login required", "forbidden", "geo", "access denied", "connect_unreachable")):
        return ErrorClass.ACCESS
    if any(k in e for k in ("permission denied", "tool denied", "not authorized", "approval required")):
        return ErrorClass.PERMISSION
    if any(k in e for k in ("timeout", "timed out", "502", "503", "504", "reset", "temporarily", "eof")):
        return ErrorClass.TRANSIENT
    return ErrorClass.FATAL


class Manager:
    def __init__(self, ledger: Ledger, executor: Executor, clock: Callable[[], float] = time.time,
                 max_concurrency: int = 2) -> None:
        self.l = ledger
        self.x = executor
        self.now = clock
        self.max_concurrency = max_concurrency

    # ------------------------------------------------------------------
    # 프로젝트 시작: 승인된 연구 범위 안에서 첫 과제만 만든다.
    def start_project(self, project: str, test: bool = False) -> Task:
        return self.l.add(Task(id=f"{project}:collect:v0", project=project, stage=Stage.COLLECT, test=test))

    # ------------------------------------------------------------------
    def tick(self) -> list[tuple[str, str]]:
        """한 번의 관리 주기. (과제ID, 실행ID) 배정 목록을 돌려준다.

        예약 실행이든 작업자 완료 알림이든 같은 tick을 호출한다. 여러 번 불러도
        결과가 같도록(멱등) 작성한다.
        """
        now = self.now()
        self._recover_leases(now)
        self._wake_waiting(now)
        running = [t for t in self.l.all() if t.status == Status.RUNNING]
        slots = self.max_concurrency - len(running)
        launched: list[tuple[str, str]] = []
        for t in self._runnable():
            if slots <= 0:
                break
            run = self._claim_and_launch(t, now)
            if run:
                launched.append((t.id, run))
                slots -= 1
        return launched

    def _runnable(self) -> list[Task]:
        done = {t.id for t in self.l.all() if t.status == Status.DONE}
        out = [t for t in self.l.all()
               if t.status == Status.QUEUED and all(d in done for d in t.depends_on)
               and (t.resume_at is None or t.resume_at <= self.now())]
        return sorted(out, key=lambda t: (t.attempts, t.id))

    def _claim_and_launch(self, t: Task, now: float) -> str | None:
        # 1) 먼저 원장에서 임대를 확보(CAS). 다른 관리자 주기가 먼저 잡았으면 포기.
        try:
            t = self.l.update(t.id, t.rev, status=Status.RUNNING, attempts=t.attempts + 1,
                              lease_owner="launching", lease_until=now + LEASE_SECONDS)
        except ConflictError:
            return None
        # 2) 실행 세션 생성. 생성 응답이 유실되면 무조건 재생성하지 않는다:
        #    lease_owner='launching'으로 남겨 두고 다음 주기에 실제 실행 여부를 확인한다.
        try:
            run = self.x.launch(t)
        except Exception as exc:  # noqa: BLE001
            self._on_error(t.id, str(exc))
            return None
        self.l.update(t.id, t.rev, lease_owner=run)
        self.l.record(f"{t.id}:launch:{t.attempts}", task=t.id, type="launch", run=run, at=now)
        return run

    # ------------------------------------------------------------------
    def heartbeat(self, task_id: str, run_id: str, checkpoint: dict[str, Any]) -> Task:
        """작업자가 저장 지점을 남긴다. 임대를 가진 실행만 쓸 수 있다."""
        t = self.l.get(task_id)
        if t.lease_owner != run_id or t.status != Status.RUNNING:
            raise ConflictError(f"{run_id} does not hold {task_id}")
        return self.l.update(task_id, t.rev, checkpoint=checkpoint,
                             lease_until=self.now() + LEASE_SECONDS)

    def save_source(self, task_id: str, run_id: str, src: Source) -> None:
        t = self.l.get(task_id)
        if t.lease_owner != run_id:
            raise ConflictError("stale writer")
        self.l.put_source(task_id, src)

    def complete(self, task_id: str, run_id: str, result: dict[str, Any], event_id: str) -> Task:
        """결과 저장 확인 후 완료. 같은 완료 통지를 여러 번 받아도 한 번만 처리."""
        t = self.l.get(task_id)
        if not self.l.record(event_id, task=task_id, type="complete", run=run_id):
            return t  # 중복 통지
        if t.status == Status.DONE:
            return t
        if t.lease_owner != run_id:
            # 임대를 잃은 늦은 실행의 결과는 채택하지 않는다(재배정된 실행이 우선).
            self.l.record(f"{event_id}:rejected", task=task_id, type="late_result_rejected", run=run_id)
            return t
        if t.stage == Stage.COLLECT:
            srcs = self.l.sources_of(task_id)
            if not any(s.status == SourceStatus.RETRIEVED for s in srcs):
                # 확보 원문이 하나도 없으면 완료가 아니라 보류(접근 문제) 또는 재시도
                return self._on_error(task_id, "access: no source retrieved")
        t = self.l.update(task_id, t.rev, status=Status.DONE, result=result,
                          lease_owner=None, lease_until=None, error_class=None, error_detail="")
        self._next_after(t)
        return t

    def fail(self, task_id: str, run_id: str, error: str) -> Task:
        t = self.l.get(task_id)
        if t.lease_owner != run_id:
            raise ConflictError("stale writer")
        return self._on_error(task_id, error)

    def _on_error(self, task_id: str, error: str) -> Task:
        t = self.l.get(task_id)
        cls = classify(error)
        now = self.now()
        common = dict(lease_owner=None, lease_until=None, error_class=cls, error_detail=error[:500])
        if cls == ErrorClass.TRANSIENT and t.attempts < t.max_attempts:
            wait = BACKOFF[min(t.attempts - 1, len(BACKOFF) - 1)] if t.attempts else BACKOFF[0]
            return self.l.update(task_id, t.rev, status=Status.QUEUED, resume_at=now + wait, **common)
        if cls == ErrorClass.LIMIT:
            # 한도 도달: 시도 횟수를 소모하지 않고 대기. 자동 과금 전환 없음.
            return self.l.update(task_id, t.rev, status=Status.WAITING_LIMIT,
                                 attempts=max(t.attempts - 1, 0), resume_at=now + LIMIT_RECHECK, **common)
        if cls == ErrorClass.ACCESS:
            return self.l.update(task_id, t.rev, status=Status.BLOCKED, **common)
        if cls == ErrorClass.PERMISSION:
            return self.l.update(task_id, t.rev, status=Status.WAITING_USER, **common)
        return self.l.update(task_id, t.rev, status=Status.FAILED, **common)

    # ------------------------------------------------------------------
    def _recover_leases(self, now: float) -> None:
        """임대 만료 과제: 실제 실행 여부를 확인한 뒤에만 재배정한다."""
        for t in self.l.all():
            if t.status != Status.RUNNING or t.lease_until is None or t.lease_until > now:
                continue
            st = self.x.status(t.lease_owner) if t.lease_owner not in (None, "launching") else "unknown"
            if st == "running":
                self.l.update(t.id, t.rev, lease_until=now + LEASE_SECONDS)  # 아직 일하는 중
                continue
            self.l.record(f"{t.id}:lease_expired:{t.attempts}", task=t.id, type="lease_expired",
                          run=t.lease_owner, observed=st, at=now)
            if t.attempts >= t.max_attempts:
                self.l.update(t.id, t.rev, status=Status.FAILED, lease_owner=None, lease_until=None,
                              error_class=ErrorClass.TRANSIENT, error_detail=f"lease expired ({st})")
            else:
                # 체크포인트는 유지 → 다음 실행이 저장 지점부터 재개
                self.l.update(t.id, t.rev, status=Status.QUEUED, lease_owner=None, lease_until=None)

    def _wake_waiting(self, now: float) -> None:
        for t in self.l.all():
            if t.status == Status.WAITING_LIMIT and t.resume_at and t.resume_at <= now:
                self.l.update(t.id, t.rev, status=Status.QUEUED)

    # ------------------------------------------------------------------
    def _next_after(self, t: Task) -> None:
        """단계 완료 → 다음 과제를 자동 생성(멱등 ID)."""
        if t.stage == Stage.REVIEW:
            verdict = t.result.get("verdict")
            if verdict == "approve":
                self.l.add(Task(id=f"{t.project}:user_review:v{t.artifact_version}", project=t.project,
                                stage=Stage.USER_REVIEW, status=Status.WAITING_USER, depends_on=[t.id],
                                artifact_version=t.artifact_version, artifact_hash=t.artifact_hash, test=t.test))
            elif verdict == "changes_requested":
                if t.artifact_version + 1 > MAX_REVISION_ROUNDS:
                    self.l.add(Task(id=f"{t.project}:user_review:v{t.artifact_version}", project=t.project,
                                    stage=Stage.USER_REVIEW, status=Status.WAITING_USER, depends_on=[t.id],
                                    artifact_version=t.artifact_version, test=t.test,
                                    error_detail="수정 한도 도달 — 사용자 판단 필요"))
                    return
                rid = f"{t.project}:revise:v{t.artifact_version + 1}"
                self.l.add(Task(id=rid, project=t.project, stage=Stage.REVISE, depends_on=[t.id],
                                artifact_version=t.artifact_version + 1,
                                checkpoint={"requested_changes": t.result.get("changes", [])}, test=t.test))
            return
        if t.stage == Stage.REVISE:
            v = t.artifact_version
            self.l.add(Task(id=f"{t.project}:review:v{v}", project=t.project, stage=Stage.REVIEW,
                            depends_on=[t.id], artifact_version=v,
                            artifact_hash=t.result.get("artifact_hash"), test=t.test))
            return
        if t.stage in PIPELINE:
            i = PIPELINE.index(t.stage)
            nxt = PIPELINE[i + 1]
            v = t.artifact_version
            self.l.add(Task(id=f"{t.project}:{nxt.value}:v{v}", project=t.project, stage=nxt,
                            depends_on=[t.id], artifact_version=v,
                            artifact_hash=t.result.get("artifact_hash"), test=t.test))


def review_allowed(reviewer_ctx: str, author_ctxs: set[str], frozen_hash: str | None, current_hash: str | None) -> tuple[bool, str]:
    """독립 검토 조건: 작성 문맥과 다른 실행 문맥 + 고정된 판본."""
    if reviewer_ctx in author_ctxs:
        return False, "작성에 참여한 실행 문맥은 검토할 수 없음"
    if not frozen_hash or frozen_hash != current_hash:
        return False, "검토 대상 판본이 고정되지 않았거나 변경됨 — 재검토 필요"
    return True, "ok"


def artifact_hash(text: str) -> str:
    return content_hash(text)
