"""OpenAI API 연구원 어댑터 (독립 검토자 1차 용도).

- API 키는 코드·저장소·로그에 두지 않는다. 세션의 HTTPS 프록시가 저장된 비밀값을 요청에 주입한다
  (request_credential로 사용자가 저장). 여기서는 형식만 맞춘 자리표시 값을 보낸다.
- 모든 호출은 Budget.reserve → 전송 → Budget.settle 순서. 차단되면 전송하지 않는다.
- 비용·쿼터 오류는 재시도하지 않는다. 일시 오류(429 rate limit 아님·5xx·시간초과)만 1회 재시도.
- 실제 엔드포인트·모델·요금은 사용자 확인 후 설정한다. 이 파일은 모의시험으로만 검증됨.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .budget import STOP_ERRORS, Budget, BudgetBlocked

PLACEHOLDER_KEY = "sk-proxy-injected"   # 프록시가 실제 값으로 교체. 실제 키 아님.
Transport = Callable[[str, dict, dict, int], tuple[int, dict]]   # (url, headers, body, timeout) -> (status, json)


def _err_text(status: int, body: dict) -> str:
    e = body.get("error") if isinstance(body, dict) else None
    if isinstance(e, dict):
        return f"{status} {e.get('code') or ''} {e.get('type') or ''} {e.get('message') or ''}".strip()
    return f"{status} {json.dumps(body)[:200]}"


def extract_text(data: dict) -> str:
    """Responses API 원 응답에서 텍스트 추출. `output_text`는 SDK 편의 필드라 원 JSON에는 없을 수 있다
    (실제 첫 호출에서 확인: 텍스트가 output[].content[].text 에만 있었음)."""
    if data.get("output_text"):
        return data["output_text"]
    parts = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    parts.append(c["text"])
    return "\n".join(parts)


def call_model(budget: Budget, transport: Transport, call_id: str, model: str, prompt: str,
               est_input_tokens: int, url: str = "https://api.openai.com/v1/responses") -> dict[str, Any]:
    """한 번의 호출. 반환: {ok, text, usage, cost, error, blocked}"""
    try:
        budget.reserve(call_id, model, est_input_tokens)
    except BudgetBlocked as b:
        return {"ok": False, "blocked": str(b), "cost": 0.0}
    headers = {"Authorization": f"Bearer {PLACEHOLDER_KEY}", "Content-Type": "application/json"}
    body = {"model": model, "input": prompt, "max_output_tokens": budget.lim.max_output_tokens,
            "store": False}
    attempts, last_err = 0, ""
    while attempts <= budget.lim.max_retries:
        attempts += 1
        try:
            status, data = transport(url, headers, body, budget.lim.timeout_s)
        except Exception as exc:  # noqa: BLE001 — 시간초과·연결 오류
            status, data = 0, {"error": {"message": str(exc)}}
        if 200 <= status < 300:
            usage = (data.get("usage") or {})
            u = {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}
            u = u if None not in u.values() else None
            cost = budget.settle(call_id, model, u)
            text = extract_text(data)
            return {"ok": True, "text": text, "usage": u, "cost": cost, "response_id": data.get("id"),
                    "status": data.get("status"), "incomplete": (data.get("incomplete_details") or {}).get("reason")}
        last_err = _err_text(status, data)
        if any(k in last_err.lower() for k in STOP_ERRORS) or status in (401, 403):
            break                                  # 비용·권한 문제: 재시도 금지
        if status == 429 or status >= 500 or status == 0:
            continue                               # 일시 오류: 상한 내 재시도
        break
    # 실패해도 요청이 처리됐을 수 있으므로 사용량을 모르면 최악값으로 정산(보수적)
    cost = budget.settle(call_id, model, None, error=last_err)
    return {"ok": False, "error": last_err, "cost": cost}
