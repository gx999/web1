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


from kernel.openai_agents import recover_pending  # noqa: E402


class Crash(BaseException):   # 프로세스 중단 모사(일반 예외 처리에 잡히지 않음)
    pass


def test_restart_mid_turn_keeps_reservation_then_settles_from_turn_usage(tmp_path):
    b = mk(tmp_path)
    calls = {"n": 0}

    def crashing(method, url, headers, body, timeout):     # 생성 성공 직후 프로세스 중단 모사
        if method == "POST":
            return 201, {"id": "sess_9"}
        raise Crash()
    try:
        run_turn(b, crashing, "c1", "m", "q", 100, sleep=lambda s: None)
    except Crash:
        pass
    b2 = mk(tmp_path)                                         # 재시작: 파일에서 예산·예약 복원
    assert "c1" in b2.s["reserved"] and b2.s["pending"]["c1"]["session_id"] == "sess_9"
    held = b2.remaining()
    assert held < b2.lim.budget_usd                           # 예약액이 잔여 예산에서 빠진 상태 유지
    r = recover_pending(b2, Fake([(200, {"data": [turn("t1", "in_progress")]})]))
    assert r[0]["action"] == "wait" and "c1" in b2.s["reserved"]
    r = recover_pending(b2, Fake([(200, {"data": [turn("t1", "completed")]})]))
    assert r[0]["action"] == "settled" and r[0]["basis"] == "usage"
    assert abs(b2.report()["spent_usd"] - 0.002) < 1e-9 and not b2.s["reserved"] and not b2.s["pending"]


def test_restart_before_session_id_settles_worst_only_after_stale(tmp_path):
    t = [1_000_000.0]
    b = Budget(str(tmp_path / "b.json"), "openai", Limits(1.0, 20, 10000, 50000), P, clock=lambda: t[0])
    b.reserve("c1", "m", 100)                                 # 전송 전 중단
    assert recover_pending(b, Fake([]))[0]["action"] == "wait"
    t[0] += 4000
    r = recover_pending(b, Fake([]))
    assert r[0]["action"] == "settled_worst" and b.report()["spent_usd"] > 0


def test_restart_no_turn_submitted_settles_zero_after_stale(tmp_path):
    t = [1_000_000.0]
    b = Budget(str(tmp_path / "b.json"), "openai", Limits(1.0, 20, 10000, 50000), P, clock=lambda: t[0])
    b.reserve("c1", "m", 100)
    b.note_pending("c1", session_id="sess_1", known_turns=["t0"])
    t[0] += 4000
    r = recover_pending(b, Fake([(200, {"data": [turn("t0", "completed")]})]))
    assert r[0]["action"] == "settled_zero_no_turn" and b.report()["spent_usd"] == 0


def test_duplicate_call_id_after_restart_is_blocked(tmp_path):
    b = mk(tmp_path)
    run_turn(b, Fake([(200, {"data": [turn("t1", "completed")]})], ITEMS), "c1", "m", "q", 100, sleep=lambda s: None)
    b2 = mk(tmp_path)
    r = run_turn(b2, Fake([]), "c1", "m", "q", 100)
    assert "중복" in r["blocked"]
