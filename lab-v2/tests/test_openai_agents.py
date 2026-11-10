"""Agents API 어댑터 모의시험 — 가짜 전송·가짜 요금. 실제 API 호출 없음."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.budget import Budget, Limits, Price  # noqa: E402
from kernel.openai_agents import run_turn  # noqa: E402

P = {"m": Price(1.0, 5.0, source="시험용 가짜 값")}


def mk(tmp_path, budget=1.0, out=10000):
    return Budget(str(tmp_path / "b.json"), "openai",
                  Limits(budget_usd=budget, max_calls_per_day=20, max_output_tokens=out, max_input_tokens=50000),
                  P, clock=lambda: 1_000_000.0)


class Fake:
    """경로별 응답 대본. turns 응답은 순서대로 소비, 마지막 것은 반복."""
    def __init__(self, turns, items=None, create=(201, {"id": "sess_1"}), events=(200, {})):
        self.turns, self.items, self.create, self.events, self.log = list(turns), items or [], create, events, []

    def __call__(self, method, url, headers, body, timeout):
        self.log.append((method, url.split("/v1")[1], headers.get("Idempotency-Key"), body))
        if method == "POST" and url.endswith("/agents/sessions"):
            return self.create
        if url.endswith("/events"):
            return self.events
        if "/turns" in url:
            return self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        if "/items" in url:
            return 200, {"data": self.items}
        raise AssertionError(url)


def turn(tid, status, inp=1000, out=200):
    return {"id": tid, "status": status, "usage": {"input_tokens": inp, "output_tokens": out}}


ITEMS = [{"type": "message", "role": "user", "turn_id": "t1", "content": [{"type": "input_text", "text": "q"}]},
         {"type": "message", "role": "assistant", "turn_id": "t1", "phase": "commentary",
          "content": [{"type": "output_text", "text": "생각 중"}]},
         {"type": "message", "role": "assistant", "turn_id": "t1", "phase": "final_answer",
          "content": [{"type": "output_text", "text": "초안 본문"}]}]


def test_new_session_runs_to_completion_with_minimal_cost_config(tmp_path):
    b = mk(tmp_path)
    http = Fake([(200, {"data": [turn("t1", "in_progress")]}), (200, {"data": [turn("t1", "completed")]})], ITEMS)
    r = run_turn(b, http, "c1", "m", "q", 100, sleep=lambda s: None)
    assert r["ok"] and r["session_id"] == "sess_1" and r["text"] == "초안 본문"
    assert abs(r["cost"] - (1000e-6 * 1 + 200e-6 * 5)) < 1e-12
    body = http.log[0][3]
    assert body["environment"] == {"type": "none"} and body["agent"]["tools"] == []
    assert body["agent"]["multi_agent"] == {"enabled": False} and http.log[0][2] == "c1"


def test_follow_up_ignores_previous_turn_and_uses_idempotency_key(tmp_path):
    b = mk(tmp_path)
    old = turn("t0", "completed")
    http = Fake([(200, {"data": [old]}), (200, {"data": [old]}), (200, {"data": [turn("t1", "completed"), old]})],
                ITEMS)
    r = run_turn(b, http, "c2", "m", "q", 100, session_id="sess_1", sleep=lambda s: None)
    assert r["turn_id"] == "t1" and r["ok"]
    sent = [x for x in http.log if x[1].endswith("/events")]
    assert sent[0][2] == "c2"


def test_cancels_when_turn_usage_exceeds_reservation(tmp_path):
    b = mk(tmp_path, out=1000)     # 최악 (100+8000)*1e-6 + 1000*5e-6 = 0.0131
    big = turn("t1", "in_progress", inp=20000, out=500)
    http = Fake([(200, {"data": [big]}), (200, {"data": [dict(big, status="cancelled")]})], ITEMS)
    r = run_turn(b, http, "c1", "m", "q", 100, sleep=lambda s: None)
    assert not r["ok"] and "예약액 초과" in r["error"]
    assert any(x[3] == {"events": [{"type": "agent.session.input.cancel"}]} for x in http.log)


def test_billing_429_on_create_kills_without_retry(tmp_path):
    b = mk(tmp_path)
    http = Fake([(200, {"data": []})], create=(429, {"error": {"code": "credit_balance_exhausted", "message": "x"}}))
    r = run_turn(b, http, "c1", "m", "q", 100, sleep=lambda s: None)
    assert not r["ok"] and b.report()["killed"]
    assert sum(1 for x in http.log if x[1] == "/agents/sessions") == 1
    assert "정지 상태" in run_turn(b, http, "c2", "m", "q", 100)["blocked"]


def test_validation_error_settles_zero(tmp_path):
    b = mk(tmp_path)
    r = run_turn(b, Fake([], create=(400, {"error": {"code": "invalid_request", "message": "bad"}})),
                 "c1", "m", "q", 100)
    assert not r["ok"] and r["cost"] == 0 and r["basis"] == "rejected_before_processing"


def test_timeout_cancels_and_charges_known_usage(tmp_path):
    b = mk(tmp_path)
    t = [0.0]

    def clock():
        t[0] += 100
        return t[0]
    http = Fake([(200, {"data": [turn("t1", "in_progress", 500, 50)]})])
    r = run_turn(b, http, "c1", "m", "q", 100, sleep=lambda s: None, clock=clock, max_wait_s=250)
    assert not r["ok"] and "시간 초과" in r["error"] and r["basis"] == "usage"


def test_usage_null_at_completion_is_repolled(tmp_path):
    b = mk(tmp_path)
    done_null = dict(turn("t1", "completed"), usage=None)
    http = Fake([(200, {"data": [done_null]}), (200, {"data": [done_null]}), (200, {"data": [turn("t1", "completed")]})],
                ITEMS)
    r = run_turn(b, http, "c1", "m", "q", 100, sleep=lambda s: None)
    assert r["basis"] == "usage" and abs(r["cost"] - 0.002) < 1e-12


def test_reconcile_replaces_worst_case_with_late_usage(tmp_path):
    b = mk(tmp_path)
    b.reserve("c1", "m", 100)
    worst = b.settle("c1", "m", None)
    b.reconcile("c1", "m", {"input_tokens": 1000, "output_tokens": 200}, "늦게 확인된 사용량")
    c = b.s["calls"][0]
    assert c["basis"] == "usage_reconciled" and c["corrections"][0]["from"] == worst
    assert abs(b.report()["spent_usd"] - 0.002) < 1e-9
