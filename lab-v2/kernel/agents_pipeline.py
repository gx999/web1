"""OpenAI 연구원 단계 연결(수집 → 초안 → 독립 검토 → 수정) — 재시작 시 이어서 실행.

- 수집은 Claude 쪽 수집기(공식 출처, 재시도·원인 구분). 연구원(OpenAI 세션)은 받은 원문만 쓴다.
- 자료 일부 미확보: 미확보 목록(원인 포함)을 프롬프트에 넣고 해당 주장·비교는 '보류(자료 미확보)'로 쓰게 한다.
  확보 자료가 0건이면 연구원을 호출하지 않고 blocked(원인 기록).
- 상태(job)는 단계마다 저장(save 콜백). 재시작하면 끝난 단계는 건너뛴다.
  시작했지만 결과를 못 남긴 단계: 예약이 남아 있으면 '복구 대기', 이미 정산됐으면 새 시도 ID로 1회 재실행.
- 호출 ID = <job>:<step>:a<시도> → 같은 단계의 중복 실행은 예산 원장에서 차단.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from collectors.law_go_kr import fetch_article
from .budget import Budget
from .openai_agents import run_turn

STEP_MAX_ATTEMPTS = 2

RULES = """연구소 공통 규칙(반드시 지킬 것):
1. 아래 [자료]로 제공된 조문 원문만 근거로 쓴다. 제공되지 않은 법령·판례·통계·사건을 지어내지 않는다.
2. 인용할 때마다 (법령명 조문번호, 시행일 YYYY-MM-DD)를 붙인다. 시행일은 자료에 적힌 값만 쓴다.
3. 현행법 기준임을 밝히고, 사건 당시 법과 다를 수 있음을 구분한다.
4. 원문에서 확인되지 않는 해석은 '해석(확인 필요)'으로 표시한다.
5. [미확보 자료]에 있는 조문에 관한 주장·비교는 하지 말고 '보류(자료 미확보: 원인)'로만 적는다.
6. 이 작업은 시스템 시험(test=true)일 수 있으며 공개·투고하지 않는다. 개인정보를 쓰지 않는다."""
DRAFTER = RULES + "\n역할: 분석·초안 연구원. 한국어로 간결하게 쓴다."
REVIEWER = RULES + """
역할: 독립 검토 연구원. 초안의 모든 주장을 [자료] 원문과 하나씩 대조한다.
출력 첫 줄은 반드시 '판정: approve' 또는 '판정: revise'.
지적사항: 번호 목록. 각 항목에 (초안 문장 요지 → 원문 근거 또는 '원문에 없음' → 수정 제안)
미확보 자료에 관한 주장을 초안이 했다면 지적한다. 모델 간 의견 일치는 검증이 아니다."""


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def collect(articles: list[tuple[str, str]], fetch) -> list[dict]:
    out = []
    for law, art in articles:
        a = fetch_article(law, art, fetch)
        out.append({"law": law, "article": art, "status": a.status, "url": a.url, "effective_date": a.effective_date,
                    "sha256": a.sha256, "chars": len(a.text), "note": a.note, "text": a.text})
    return out


def source_pack(sources: list[dict]) -> str:
    ok = [s for s in sources if s["status"] == "retrieved"]
    miss = [s for s in sources if s["status"] != "retrieved"]
    parts = [f"[자료 {i + 1}] {s['law']} {s['article']} (시행일 {s['effective_date']}, 출처 {s['url']})\n{s['text']}"
             for i, s in enumerate(ok)]
    if miss:
        parts.append("[미확보 자료]\n" + "\n".join(f"- {s['law']} {s['article']}: {s['status']} ({s['note'][:120]})"
                                              for s in miss))
    return "\n\n".join(parts)


def run_chain(job: dict, budget: Budget, http, fetch, save: Callable[[dict], None],
              write_text: Callable[[str, str], str], models: dict[str, str], clock=time.time,
              turn_kwargs: dict | None = None) -> dict:
    """job: {"id", "question", "articles": [[law, art], ...], "steps": {}, "state"} — 제자리 갱신·저장."""
    steps: dict[str, Any] = job.setdefault("steps", {})
    kw = turn_kwargs or {}

    def iso():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock()))

    def finish(state: str, why: str = "") -> dict:
        job["state"], job["state_reason"], job["updated_at"] = state, why, iso()
        save(job)
        return job

    # 1) 수집 ----------------------------------------------------------------
    if steps.get("collect", {}).get("status") != "done":
        srcs = collect([tuple(a) for a in job["articles"]], fetch)
        for s in srcs:
            if s["text"]:
                s["path"] = write_text(f"sources/{s['law']}_{s['article']}.txt", s["text"])
        steps["collect"] = {"status": "done", "at": iso(),
                            "sources": [{k: v for k, v in s.items() if k != "text"} for s in srcs]}
        job["_texts"] = {f"{s['law']}|{s['article']}": s["text"] for s in srcs}
        save(job)
    srcs = [dict(s, text=job.get("_texts", {}).get(f"{s['law']}|{s['article']}", "")) for s in steps["collect"]["sources"]]
    if not any(s["status"] == "retrieved" for s in srcs):
        return finish("blocked", "확보한 원문 0건: " + "; ".join(f"{s['article']} {s['status']}" for s in srcs))
    pack = source_pack(srcs)
    est = len(pack) + 2000

    def step(name: str, model: str, text: str, instructions: str = "", session_from: str | None = None):
        cur = steps.get(name, {})
        if cur.get("status") == "done":
            return cur
        attempt = cur.get("attempt", 0)
        if cur.get("status") == "started":
            cid = cur["call_id"]
            if cid in budget.s["reserved"]:
                return {"status": "recovering", "call_id": cid}      # 복구(recover_pending) 뒤 다음 실행에서 판단
            if attempt >= STEP_MAX_ATTEMPTS:
                return {"status": "failed", "error": "결과 유실 반복 — 시도 상한"}
        attempt += 1
        cid = f"{job['id']}:{name}:a{attempt}"
        steps[name] = {"status": "started", "call_id": cid, "attempt": attempt, "at": iso(), "model": model}
        save(job)
        sid = steps[session_from]["session_id"] if session_from else None
        r = run_turn(budget, http, cid, model, text, est, session_id=sid, instructions=instructions,
                     metadata={"lab": "v2", "job": job["id"], "step": name, "test": str(job.get("test", True)).lower()},
                     **kw)
        rec = {k: v for k, v in r.items() if k != "text"}
        rec.update(status="done" if r.get("ok") else ("blocked_budget" if r.get("blocked") else "failed"),
                   call_id=cid, attempt=attempt, model=model, at=iso())
        if r.get("text"):
            rec["path"] = write_text(f"{name}.md", r["text"])
            rec["sha256"], rec["chars"] = sha(r["text"]), len(r["text"])
            rec["text"] = r["text"]
        steps[name] = rec
        save(job)
        return rec

    d0 = step("draft", models["draft"], job["question"] + "\n\n" + pack, DRAFTER)
    if d0["status"] != "done":
        return finish({"recovering": "running"}.get(d0["status"], d0["status"]), d0.get("error") or d0.get("blocked", ""))
    rv = step("review", models["review"], "[초안]\n" + d0["text"] + "\n\n" + pack, REVIEWER)
    if rv["status"] != "done":
        return finish({"recovering": "running"}.get(rv["status"], rv["status"]), rv.get("error") or rv.get("blocked", ""))
    verdict = "approve" if rv["text"].lstrip().startswith("판정: approve") else "revise"
    job["verdict"] = verdict
    if verdict == "approve":
        return finish("done", "검토 승인 — 수정 생략")
    d1 = step("revise", models["draft"],
              "독립 검토 의견이다. 원문과 대조해 타당한 지적만 반영해 수정본 전체를 다시 써라. "
              "반영하지 않은 지적은 이유를 끝에 적어라.\n\n" + rv["text"], session_from="draft")
    if d1["status"] != "done":
        return finish({"recovering": "running"}.get(d1["status"], d1["status"]), d1.get("error") or d1.get("blocked", ""))
    return finish("done", "검토 반영 수정본 완료(재검토 없음)")
