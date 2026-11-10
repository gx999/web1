"""OpenAI Agents API 연구원 시범 실행 (test=true, 실제 연구 성과 아님).

흐름: Claude 쪽 수집기가 law.go.kr에서 조문 원문 수집 → [초안 연구원 세션] 초안
      → [검토 연구원 세션(별도 세션·다른 모델)] 원문 대조 검토 → [초안 세션 이어서] 수정.
결과·사용량은 비공개 원장 저장소의 tests/agents_pilot/ 와 ops/budget_openai_agents.json 에 기록.

사용: python tools/agents_pilot.py <원장 저장소 경로>
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collectors.law_go_kr import fetch_article, urllib_fetch  # noqa: E402
from kernel.budget import Budget, Limits, Price  # noqa: E402
from kernel.openai_agents import run_turn, urllib_transport  # noqa: E402

SRC = "https://developers.openai.com/api/docs/pricing (Standard, short context) 2026-11-10 확인"
PRICES = {
    "gpt-6.1-terra": Price(1.00, 5.00, source=SRC),
    "gpt-6-sol": Price(2.00, 10.00, source=SRC),
}
LIMITS = Limits(budget_usd=3.0, max_calls_per_day=30, max_output_tokens=16000, max_input_tokens=60000,
                max_concurrency=2, max_retries=1, timeout_s=60)
DRAFT_MODEL, REVIEW_MODEL = "gpt-6.1-terra", "gpt-6-sol"

ARTICLES = [("정보통신망 이용촉진 및 정보보호 등에 관한 법률", "제48조의3"),
            ("정보통신망 이용촉진 및 정보보호 등에 관한 법률", "제48조의4"),
            ("개인정보 보호법", "제29조"),
            ("개인정보 보호법", "제34조")]

RULES = """연구소 공통 규칙(반드시 지킬 것):
1. 아래 [자료]로 제공된 조문 원문만 근거로 쓴다. 제공되지 않은 법령·판례·통계·사건을 지어내지 않는다.
2. 인용할 때마다 (법령명 조문번호, 시행일 YYYY-MM-DD)를 붙인다. 시행일은 자료에 적힌 값만 쓴다.
3. 현행법 기준임을 밝히고, 사건 당시 법과 다를 수 있음을 구분한다.
4. 원문에서 확인되지 않는 해석은 '해석(확인 필요)'으로 표시한다.
5. 이 작업은 시스템 시험(test=true)이며 공개·투고하지 않는다. 개인정보를 쓰지 않는다."""

DRAFTER = RULES + "\n역할: 분석·초안 연구원. 한국어로 간결하게 쓴다."
REVIEWER = RULES + """
역할: 독립 검토 연구원. 초안의 모든 주장을 [자료] 원문과 하나씩 대조한다.
출력 형식:
판정: approve | revise
지적사항: 번호 목록. 각 항목에 (초안 문장 요지 → 원문 근거 또는 '원문에 없음' → 수정 제안)
모델 간 의견 일치는 검증이 아니며, 원문 대조 결과만 근거로 판정한다."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(repo: str) -> int:
    run_id = "AP1-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(repo, "tests", "agents_pilot", run_id)
    os.makedirs(out_dir, exist_ok=True)
    budget = Budget(os.path.join(repo, "ops", "budget_openai_agents.json"), "openai", LIMITS, PRICES)
    log: dict = {"run_id": run_id, "test": True, "started_at": now(), "steps": [],
                 "config": {"environment": "none", "tools": [], "multi_agent": False,
                            "draft_model": DRAFT_MODEL, "review_model": REVIEW_MODEL,
                            "internal_budget_usd": LIMITS.budget_usd}}

    def save():
        log["budget"] = budget.report()
        with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)

    # 1) 수집 (Claude 쪽 수집기, 공식 출처) ---------------------------------
    cache: dict = {}

    def fetch_once(url):            # 같은 법령 본문을 반복 요청하지 않음(실측: 4번째 연속 요청에서 연결 실패)
        if url not in cache or cache[url][0] != 200:
            cache[url] = urllib_fetch(url)
        return cache[url]

    sources = []
    for name, art in ARTICLES:
        a = fetch_article(name, art, fetch_once)
        sources.append({"law": name, "article": art, "status": a.status, "url": a.url,
                        "effective_date": a.effective_date, "sha256": a.sha256, "chars": len(a.text), "text": a.text})
    log["steps"].append({"stage": "collect", "by": "claude_collector", "at": now(),
                         "sources": [{k: v for k, v in s.items() if k != "text"} for s in sources]})
    ok_src = [s for s in sources if s["status"] == "retrieved"]
    if not ok_src:
        log["result"] = "blocked: 원문 수집 실패"
        save()
        return 1
    pack = "\n\n".join(f"[자료 {i + 1}] {s['law']} {s['article']} (시행일 {s['effective_date']}, 출처 {s['url']})\n{s['text']}"
                       for i, s in enumerate(ok_src))
    est = len(pack) + 2000   # 한글 1자 ≈ 1토큰 이하로 보수 추정

    def step(stage, call_id, model, text, session_id=None, instructions=""):
        r = run_turn(budget, urllib_transport, f"{run_id}:{call_id}", model, text, est, session_id=session_id,
                     instructions=instructions, metadata={"lab": "v2", "run": run_id, "stage": stage, "test": "true"})
        rec = {"stage": stage, "model": model, "at": now(), **{k: v for k, v in r.items() if k != "text"},
               "text_sha256": hashlib.sha256((r.get("text") or "").encode()).hexdigest(),
               "text_chars": len(r.get("text") or "")}
        log["steps"].append(rec)
        if r.get("text"):
            with open(os.path.join(out_dir, f"{call_id}.md"), "w", encoding="utf-8") as f:
                f.write(r["text"])
        save()
        print(json.dumps({k: rec.get(k) for k in ("stage", "ok", "session_id", "turn_status", "usage", "cost",
                                                  "error", "blocked", "text_chars")}, ensure_ascii=False))
        return r

    q = ("과제(T24A 축약판): 침해사고 발생 시 정보통신서비스 제공자 등의 신고·원인분석 의무(정보통신망법)와 "
         "개인정보처리자의 안전조치·유출 통지·신고 의무(개인정보 보호법)를 비교하라. "
         "특히 '자료 보전'과 관련된 내용이 각 조문에 있는지 없는지를 원문 근거로 밝혀라. 900자 이내.\n\n" + pack)
    d0 = step("draft", "draft_v0", DRAFT_MODEL, q, instructions=DRAFTER)
    if not d0.get("ok"):
        log["result"] = "draft 실패"
        save()
        return 1
    rv = step("review", "review_v0", REVIEW_MODEL, "[초안]\n" + d0["text"] + "\n\n" + pack, instructions=REVIEWER)
    if not rv.get("ok"):
        log["result"] = "review 실패"
        save()
        return 1
    if rv["text"].lstrip().startswith("판정: approve"):
        log["result"] = "completed (검토 승인 → 수정 단계 생략)"
        log["finished_at"] = now()
        save()
        return 0
    d1 = step("revise", "draft_v1", DRAFT_MODEL,
              "독립 검토 의견이다. 원문과 대조해 타당한 지적만 반영해 수정본 전체를 다시 써라. "
              "반영하지 않은 지적은 이유를 끝에 적어라.\n\n" + rv["text"], session_id=d0["session_id"])
    log["result"] = "completed" if d1.get("ok") else "revise 실패"
    log["finished_at"] = now()
    save()
    return 0 if d1.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
