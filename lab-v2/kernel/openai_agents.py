"""OpenAI Agents API(베타) 연구원 어댑터.

- 연구원 1명 = Agents API 세션 1개(세션 ID·대화 기록이 OpenAI 쪽에 보존). 역할마다 별도 세션.
- 비용 최소 구성: environment none(샌드박스 컨테이너 요금 없음), tools [](웹 검색 등 내장 도구 없음),
  multi_agent 꺼짐(하위 에이전트 없음), service_tier "default"(우선 처리 요금 아님).
- Agents API에는 요청별 출력 상한 필드가 없다. 그래서
  ① 예약은 '가정한 최악값'(입력 추정 + 하네스 기본 지시 + 출력 가정)으로 하고
  ② 진행 중 턴의 사용량이 예약액을 넘으면 즉시 취소하며
  ③ 정산은 턴의 실제 usage로 한다(캐시 입력도 정가로 계산 — 보수적).
- 결과 수신: 웹훅은 우리 쪽 공개 엔드포인트가 필요하므로 쓰지 않고, 턴 상태를 조회(polling)한다.
- 메시지 제출마다 Idempotency-Key = call_id (재시도해도 중복 턴이 생기지 않게).
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from .budget import STOP_ERRORS, Budget, BudgetBlocked
from .openai_worker import PLACEHOLDER_KEY

API = "https://api.openai.com/v1"
# (method, url, headers, body|None, timeout) -> (status, json)
Transport = Callable[[str, str, dict, dict | None, int], tuple[int, dict]]
TURN_END = ("completed", "failed", "cancelled")
HARNESS_OVERHEAD_TOKENS = 8000   # 하네스 기본 지시문 등(미공개) 가정치 — 첫 실측 후 조정


def _headers(idem: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {PLACEHOLDER_KEY}", "Content-Type": "application/json",
         "OpenAI-Beta": "agents=v1"}
    if idem:
        h["Idempotency-Key"] = idem
    return h


def agent_config(model: str, instructions: str) -> dict:
    return {"model": model, "instructions": instructions, "tools": [],
            "multi_agent": {"enabled": False}, "reasoning": {"effort": "low"},
            "service_tier": "default"}


def _err(status: int, data: dict) -> str:
    e = data.get("error") if isinstance(data, dict) else None
    if isinstance(e, dict):
        return f"{status} {e.get('code') or ''} {e.get('message') or ''}".strip()
    if isinstance(e, str):
        return f"{status} {e}"
    return f"{status} {json.dumps(data, ensure_ascii=False)[:200]}"


def turn_text(items: list[dict], turn_id: str) -> str:
    msgs = [i for i in items if i.get("type") == "message" and i.get("role") == "assistant"
            and i.get("turn_id") == turn_id]
    final = [m for m in msgs if m.get("phase") == "final_answer"] or msgs
    out = []
    for m in final:
        for c in m.get("content") or []:
            if c.get("type") in ("output_text", "text") and c.get("text"):
                out.append(c["text"])
    return "\n".join(out)


def _usage(u: dict | None) -> dict | None:
    if not u or u.get("input_tokens") is None or u.get("output_tokens") is None:
        return None
    return {"input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
            "cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
            "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)}


def run_turn(budget: Budget, http: Transport, call_id: str, model: str, text: str, est_input_tokens: int,
             session_id: str | None = None, instructions: str = "", metadata: dict | None = None,
             poll_s: float = 3.0, max_wait_s: int = 600, usage_retries: int = 10, sleep=time.sleep, clock=time.time) -> dict[str, Any]:
    """새 세션(session_id=None) 또는 기존 세션에 메시지 1건 → 턴 1개 완료까지. 반환 dict는 원장에 그대로 기록 가능."""
    try:
        worst = budget.reserve(call_id, model, est_input_tokens + HARNESS_OVERHEAD_TOKENS)
    except BudgetBlocked as b:
        return {"ok": False, "blocked": str(b), "cost": 0.0, "session_id": session_id}
    t = budget.lim.timeout_s
    msg = [{"role": "user", "content": [{"type": "input_text", "text": text}]}]

    # 1) 제출 -------------------------------------------------------------
    known: set[str] = set()
    if session_id is None:
        body = {"agent": agent_config(model, instructions), "environment": {"type": "none"},
                "input": msg, "metadata": metadata or {}}
        st, data = _send(http, "POST", f"{API}/agents/sessions", _headers(call_id), body, t, budget.lim.max_retries)
        if not 200 <= st < 300:
            return _fail(budget, call_id, model, session_id, _err(st, data), charged_worst=st not in (400, 401, 403, 404, 422))
        session_id = data["id"]
    else:
        st, data = _send(http, "GET", f"{API}/agents/sessions/{session_id}/turns?order=desc&limit=20",
                         _headers(), None, t, budget.lim.max_retries)
        known = {x["id"] for x in data.get("data", [])} if 200 <= st < 300 else set()
        body = {"events": [{"type": "agent.session.input.message", "input": msg}]}
        st, data = _send(http, "POST", f"{API}/agents/sessions/{session_id}/events", _headers(call_id), body, t,
                         budget.lim.max_retries)
        if not 200 <= st < 300:
            return _fail(budget, call_id, model, session_id, _err(st, data), charged_worst=st not in (400, 401, 403, 404, 422))

    # 2) 턴 완료 대기(조회) -------------------------------------------------
    start, turn, cancelled_for_cost = clock(), None, False
    while True:
        st, data = _send(http, "GET", f"{API}/agents/sessions/{session_id}/turns?order=desc&limit=5",
                         _headers(), None, t, budget.lim.max_retries)
        if 200 <= st < 300:
            new = [x for x in data.get("data", []) if x["id"] not in known and not x.get("subagent_id")]
            if new:
                turn = new[0]
                u = _usage(turn.get("usage"))
                if (u and not cancelled_for_cost
                        and budget._cost(model, u["input_tokens"], u["output_tokens"]) > worst):
                    _send(http, "POST", f"{API}/agents/sessions/{session_id}/events", _headers(),
                          {"events": [{"type": "agent.session.input.cancel"}]}, t, 0)
                    cancelled_for_cost = True
                if turn.get("status") in TURN_END:
                    break
        if clock() - start > max_wait_s:
            _send(http, "POST", f"{API}/agents/sessions/{session_id}/events", _headers(),
                  {"events": [{"type": "agent.session.input.cancel"}]}, t, 0)
            return _fail(budget, call_id, model, session_id, f"턴 대기 시간 초과({max_wait_s}s) — 취소 요청함",
                         usage=_usage((turn or {}).get("usage")), turn=turn)
        sleep(poll_s)

    # 3) 결과·정산 ---------------------------------------------------------
    u = _usage(turn.get("usage"))
    for _ in range(usage_retries):          # 실측: 완료 직후 usage가 null이었다가 수 초 뒤 채워짐
        if u:
            break
        sleep(poll_s)
        st, data = _send(http, "GET", f"{API}/agents/sessions/{session_id}/turns?order=desc&limit=5",
                         _headers(), None, t, budget.lim.max_retries)
        same = [x for x in data.get("data", []) if x["id"] == turn["id"]] if 200 <= st < 300 else []
        if same:
            u = _usage(same[0].get("usage"))
    err = turn.get("error") or {}
    err_s = f"{err.get('code', '')} {err.get('message', '')}".strip() if isinstance(err, dict) else str(err)
    if cancelled_for_cost:
        err_s = ("예약액 초과로 취소 " + err_s).strip()
    cost = budget.settle(call_id, model, u, error=err_s or None)
    st, items = _send(http, "GET", f"{API}/agents/sessions/{session_id}/items?order=asc&limit=100",
                      _headers(), None, t, budget.lim.max_retries)
    text_out = turn_text(items.get("data", []) if 200 <= st < 300 else [], turn["id"])
    return {"ok": turn["status"] == "completed" and not cancelled_for_cost, "session_id": session_id,
            "turn_id": turn["id"], "turn_status": turn["status"], "text": text_out, "usage": u, "cost": cost,
            "basis": "usage" if u else "reserved_worst_case", "error": err_s,
            "started_at": turn.get("started_at"), "completed_at": turn.get("completed_at")}


def _send(http: Transport, method: str, url: str, headers: dict, body: dict | None, timeout: int,
          retries: int) -> tuple[int, dict]:
    """일시 오류(5xx·연결)만 재시도. 같은 Idempotency-Key를 재사용하므로 중복 제출이 생기지 않는다.
    429는 비용·쿼터 코드일 수 있어 재시도하지 않는다."""
    for _ in range(retries + 1):
        try:
            st, data = http(method, url, headers, body, timeout)
        except Exception as exc:  # noqa: BLE001
            st, data = 0, {"error": {"message": str(exc)}}
        if st == 0 or st >= 500:
            continue
        return st, data
    return st, data


def _fail(budget: Budget, call_id: str, model: str, session_id: str | None, err: str,
          charged_worst: bool = True, usage: dict | None = None, turn: dict | None = None) -> dict[str, Any]:
    if usage:
        cost = budget.settle(call_id, model, usage, error=err)
        basis = "usage"
    elif charged_worst:
        cost = budget.settle(call_id, model, None, error=err)   # 처리됐을 수 있음 → 최악값
        basis = "reserved_worst_case"
    else:   # 요청 형식·권한 오류(4xx)는 처리 전 거부 → 0원 정산(오류 코드가 비용 정지 대상이면 정지)
        cost = budget.settle(call_id, model, {"input_tokens": 0, "output_tokens": 0}, error=err)
        basis = "rejected_before_processing"
    if any(k in err.lower() for k in STOP_ERRORS) and not budget.s.get("killed"):
        budget.kill(f"제공자 비용·쿼터 오류: {err[:120]}")
    return {"ok": False, "error": err, "cost": cost, "basis": basis, "session_id": session_id,
            "turn_id": (turn or {}).get("id")}


def urllib_transport(method: str, url: str, headers: dict, body: dict | None, timeout: int) -> tuple[int, dict]:
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": {"message": raw[:300].decode("utf-8", "ignore")}}
