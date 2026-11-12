"""지피 지시(Notion 「연구 지시함」 행) ↔ 실행 결과 연결.

단일 작성자: 이 모듈의 기록(status/directives/)은 Claude 관리자만 쓴다. 지피는 Notion에만 쓰고 GitHub은 읽는다.

지시 1건 = status/directives/<요청ID>.json
  directive_id   지피가 정한 요청ID(Notion '요청ID'). 비어 있으면 거절.
  notion_row     Notion 행 ID(같은 행 재처리 방지의 기준)
  content_sha256 접수 시점 내용 해시(요청ID·종류·내용·대상·우선순위·시험)
  state          received | accepted | running | done | failed | blocked | needs_owner | rejected | answered
                 | waiting_user | waiting_limit   (원본 값 그대로. 다른 상태로 합치지 않음)
  history        [{at, state, reason}]  상태가 바뀔 때만 추가
  tasks / jobs   이 지시로 생긴 원장 과제 ID / OpenAI 연구원 작업 ID
  evidence       [{kind, path, sha256, status}]  근거(수집한 원문 등)
  results        [{kind, path, sha256}]  산출물(초안·검토·수정본)
status/directives/index.json = 전체 목록(요청ID, state, updated_at) — 지피가 먼저 읽는 파일.

중복·충돌 규칙
- 같은 Notion 행: 내용 해시가 같으면 무시(duplicate). 접수 후 내용이 바뀌면 다시 실행하지 않고
  history에 edited_after_intake를 남긴다(새 지시는 새 행으로).
- 다른 행이 이미 쓰인 요청ID를 쓰면 거절(요청ID 중복)하고 별도 파일로 기록.
- 원장 쓰기는 RepoLedger(git 푸시 거절 → rebase → 재시도, 같은 파일 충돌은 다음 주기에 다시 판단).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Callable

from .inbox import InboxRow, process
from .model import Status

SAFE = re.compile(r"[^0-9A-Za-z가-힣._-]")
END = {"done", "failed", "blocked", "needs_owner", "rejected", "answered"}


_MD_ESC = re.compile(r"\\([\\_*`\[\]()#+\-.!|~>{}])")
_BR = re.compile(r"<br\s*/?>", re.I)


def normalize_row(row: dict) -> dict:
    """Notion 조회(rows 모드)는 텍스트를 마크다운으로 준다: 밑줄 앞 역슬래시(openai\\_chain), 줄바꿈 <br />.
    실측(2026-11-10 T24-1 행)에 맞춰 원래 글자로 되돌린다. 숫자·체크박스 등은 그대로."""
    import html as _html
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and k != "url":
            v = _html.unescape(_MD_ESC.sub(r"\1", _BR.sub("\n", v)))
        out[k] = v
    return out


def content_sha(row: dict) -> str:
    keys = ("요청ID", "종류", "내용", "대상", "우선순위", "시험")
    return hashlib.sha256(json.dumps({k: row.get(k) for k in keys}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def row_id(row: dict) -> str:
    return row["url"].rstrip("/").split("/")[-1].split("?")[0]


class DirectiveStore:
    def __init__(self, root: str, write_json: Callable[[str, Any, str], None]) -> None:
        self.root, self.write_json = root, write_json

    def _rel(self, did: str) -> str:
        return f"status/directives/{SAFE.sub('_', did)}.json"

    def all(self, include_rejected_rows: bool = True) -> list[dict]:
        """_dup_*/_norid_* = 요청ID 중복·누락으로 거절된 행(정식 지시가 아님)."""
        d = os.path.join(self.root, "status", "directives")
        if not os.path.isdir(d):
            return []
        out = []
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or f == "index.json" or (f.startswith("_") and not include_rejected_rows):
                continue
            x = json.load(open(os.path.join(d, f), encoding="utf-8"))
            x["_file"] = f"status/directives/{f}"
            out.append(x)
        return out

    def get(self, did: str) -> dict | None:
        p = os.path.join(self.root, self._rel(did))
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

    def put(self, d: dict, rel: str | None = None) -> None:
        rel = rel or d.pop("_file", None) or self._rel(d["directive_id"])
        d.pop("_file", None)
        self.write_json(rel, d, f"directive: {d['directive_id']} {d['state']}")

    def set_state(self, d: dict, state: str, reason: str, now: str) -> bool:
        d["state_reason"] = reason[:300]
        if d["state"] == state and d["history"]:
            return False                       # 상태가 같으면 history를 늘리지 않음(사유만 갱신)
        d["state"], d["updated_at"] = state, now
        d["history"].append({"at": now, "state": state, "reason": reason[:300]})
        return True

    def write_index(self, now: str) -> None:
        rows = [{"directive_id": d["directive_id"], "state": d["state"], "updated_at": d["updated_at"],
                 "test": d.get("test"), "path": d["_file"]} for d in self.all(include_rejected_rows=False)]
        bad = [{"directive_id": d["directive_id"], "notion_row": d["notion_row"], "state": d["state"],
                "reason": d.get("state_reason", ""), "path": d["_file"]}
               for d in self.all() if d["_file"].split("/")[-1].startswith("_")]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
        self.write_json("status/directives/index.json",
                        {"observed_at": now, "writer": "claude_manager", "total": len(rows), "counts": counts,
                         "rows": rows, "rejected_rows": bad}, f"directives index {now}")


def intake(rows: list[dict], store: DirectiveStore, ledger, approved_scopes: list[str], now: str,
           on_openai_job: Callable[[dict, dict], str] | None = None) -> list[dict]:
    """Notion 행 목록(조회 결과 그대로) → 지시 기록. 반환: 행별 처리 결과."""
    out = []
    by_row = {d["notion_row"]: d for d in store.all()}
    for row in (normalize_row(r) for r in rows):
        rid, did, h = row_id(row), (row.get("요청ID") or "").strip(), content_sha(row)
        prev = by_row.get(rid)
        if prev:
            if prev["content_sha256"] == h:
                out.append({"row": rid, "directive_id": prev["directive_id"], "result": "duplicate"})
            else:
                if not any(x["state"] == "edited_after_intake" and x["reason"].endswith(h[:12])
                           for x in prev["history"]):
                    prev["history"].append({"at": now, "state": "edited_after_intake",
                                            "reason": f"접수 후 Notion 내용 변경 — 재실행 안 함, 새 행 필요. 새 해시 {h[:12]}"})
                    prev["updated_at"] = now
                    store.put(prev)
                out.append({"row": rid, "directive_id": prev["directive_id"], "result": "edited_after_intake"})
            continue
        base = {"directive_id": did or f"(빈 요청ID){rid[:8]}", "notion_row": rid, "notion_url": row.get("url"),
                "kind": row.get("종류"), "content": (row.get("내용") or "")[:2000], "target": row.get("대상") or "",
                "author": row.get("작성자"), "test": row.get("시험") == "__YES__",
                "notion_created": row.get("작성 시각"), "content_sha256": h, "received_at": now,
                "updated_at": now, "state": "received", "history": [{"at": now, "state": "received", "reason": "지시함에서 읽음"}],
                "tasks": [], "jobs": [], "evidence": [], "results": []}
        if not did:
            store.set_state(base, "rejected", "요청ID 없음", now)
            store.put(base, f"status/directives/_norid_{rid[:12]}.json")
            out.append({"row": rid, "directive_id": base["directive_id"], "result": "rejected"})
            continue
        if store.get(did):
            store.set_state(base, "rejected", f"요청ID 중복 — 이미 다른 행({store.get(did)['notion_row'][:8]})이 사용", now)
            store.put(base, f"status/directives/_dup_{SAFE.sub('_', did)}_{rid[:8]}.json")
            out.append({"row": rid, "directive_id": did, "result": "rejected_duplicate_id"})
            continue
        if base["kind"] == "report_done":   # 지피가 사용자에게 결과를 보고했다는 표시(지피는 GitHub 쓰기 권한이 없어 지시함으로 알림)
            tgt = ((base["target"] or base["content"]).strip().split() or [""])[0]
            ok = mark_reported(store, tgt, now)
            store.set_state(base, "done" if ok else "rejected",
                            f"지시 {tgt} 결과 보고 완료 표시" if ok else f"보고서 목록에 {tgt!r} 없음(또는 이미 표시됨)", now)
            store.put(base)
            out.append({"row": rid, "directive_id": did, "result": base["state"]})
            continue
        if base["target"] == "openai_chain" and base["kind"] in ("new_goal", "new_task") and on_openai_job:
            # 승인 범위·금지 표현 검사는 일반 규칙과 같게 먼저 적용
            # 연구원 사슬은 도구 없이 글만 쓴다 → 행동 위험어(공개·결제·인증 등) 검사는 지시 첫 줄(무엇을 하라는지)에만 적용.
            # 본문의 연구 대상 설명(예: '인증 의무화')이 오탐으로 보류되던 문제(G-20261112-AML-01) 해결.
            head = (base["content"] or "").strip().splitlines()[0] if (base["content"] or "").strip() else ""
            pre = process([InboxRow(rid, "new_goal", head, "", 3)],
                          _NullLedger(), set(), approved_scopes)[0]
            if pre.result != "accepted":
                store.set_state(base, pre.result, pre.reason, now)
            else:
                jid = on_openai_job(base, row)
                base["jobs"].append(jid)
                store.set_state(base, "accepted", f"OpenAI 연구원 작업 {jid} 생성", now)
        else:
            o = process([InboxRow(rid, base["kind"] or "", base["content"], base["target"],
                                  int(row.get("우선순위") or 3))], ledger, set(), approved_scopes)[0]
            base["tasks"] = o.task_ids
            store.set_state(base, o.result if o.result != "duplicate" else "rejected", o.reason, now)
            if o.result == "answered":
                base["results"].append({"kind": "answer", "path": "status/summary.json"})
        store.put(base)
        out.append({"row": rid, "directive_id": did, "result": base["state"]})
    return out


def mark_reported(store: "DirectiveStore", did: str, now: str) -> bool:
    """status/reports.json 의 해당 보고서에 지피 보고 시각을 적는다. 이미 적혀 있거나 없으면 False."""
    p = os.path.join(store.root, "status", "reports.json")
    if not did or not os.path.exists(p):
        return False
    idx = json.load(open(p, encoding="utf-8"))
    hit = next((r for r in idx.get("reports", []) if r.get("directive_id") == did and not r.get("reported_by_gippy")), None)
    if not hit:
        return False
    hit["reported_by_gippy"] = now
    store.write_json("status/reports.json", idx, f"reports: {did} reported by gippy")
    d = store.get(did)
    if d:
        d["history"].append({"at": now, "state": "reported", "reason": "지피가 사용자에게 결과 보고"})
        d["updated_at"] = now
        store.put(d)
    return True


class _NullLedger:
    """승인 범위 검사만 하고 과제는 만들지 않기 위한 자리표시 원장."""
    tasks: dict = {}

    def add(self, t):
        return t


TASK_TO_DIRECTIVE = {Status.RUNNING: "running", Status.QUEUED: "accepted", Status.FAILED: "failed",
                     Status.BLOCKED: "blocked", Status.WAITING_USER: "waiting_user",
                     Status.WAITING_LIMIT: "waiting_limit", Status.DONE: "done", Status.CANCELLED: "failed"}
PRIORITY = ["running", "waiting_limit", "accepted", "failed", "blocked", "waiting_user", "done"]


def refresh(store: DirectiveStore, ledger, jobs: dict[str, dict], now: str) -> list[str]:
    """원장 과제·OpenAI 작업 상태로 지시 상태를 다시 계산. 종결 상태는 바꾸지 않는다. 바뀐 지시 ID 반환."""
    changed = []
    for d in store.all(include_rejected_rows=False):
        if d["state"] in END or d["state"] == "received":
            continue
        before = json.dumps(d, ensure_ascii=False, sort_keys=True)
        states, reasons = [], []
        for jid in d.get("jobs", []):
            j = jobs.get(jid)
            if not j:
                continue
            states.append(j.get("state", "accepted"))
            reasons.append(f"{jid}: {j.get('state_reason', '')}")
            d["evidence"] = [{"kind": "statute", "law": s["law"], "article": s["article"], "status": s["status"],
                              "path": s.get("path"), "sha256": s.get("sha256"), "note": s.get("note", "")[:120]}
                             for s in j.get("steps", {}).get("collect", {}).get("sources", [])]
            d["results"] = [{"kind": k, "path": v.get("path"), "sha256": v.get("sha256"), "session_id": v.get("session_id"),
                             "model": v.get("model")} for k, v in j.get("steps", {}).items()
                            if k != "collect" and v.get("path")]
        for tid in d.get("tasks", []):
            try:
                t = ledger.get(tid)
            except (KeyError, FileNotFoundError):
                continue
            states.append(TASK_TO_DIRECTIVE.get(t.status, "running"))
            reasons.append(f"{tid}: {t.status.value}")
        if not states:
            continue
        # 여러 과제·작업이면 진행 중인 것이 하나라도 있으면 running 등 — 우선순위 순서로 대표값 선택
        rep = next(s for s in PRIORITY if s in states) if all(s in PRIORITY for s in states) else states[0]
        if all(s == "done" for s in states):
            rep = "done"
        store.set_state(d, rep, "; ".join(reasons)[:300], now)
        if json.dumps(d, ensure_ascii=False, sort_keys=True) != before:
            store.put(d)
            changed.append(d["directive_id"])
    store.write_index(now)
    return changed
