"""24시간 시험 보호장치 모의시험 — 잠금 연장·인수, 시험 상한, 불확실 호출 확인, 유휴 시간 호출 0회.
실제 API·예약 없음."""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_directives import CHAIN, FakeOpenAI, _clone, _origin, law_site, row  # noqa: E402
from collectors.retry import RetryingFetch  # noqa: E402
from kernel.budget import Budget, Limits, Price  # noqa: E402
from kernel.openai_agents import recover_pending  # noqa: E402
from tools import manager_run as mr  # noqa: E402

T0 = 1_800_000_000
GATE24 = {"enabled": True, "credit_org_project": "x", "credit_balance_checked": "x", "auto_recharge_off": "x",
          "payment_method_status": "x", "project_hard_limit_usd": 5, "confirmed_by_user_at": "x",
          "budget_file": "ops/budget_openai_24h.json", "budget_usd": 1.0, "max_calls_total": 8,
          "max_calls_per_day": 8, "max_concurrency": 1, "max_retries": 1, "max_jobs_total": 1,
          "require_test_directive": True, "max_articles": 2,
          "window_start_epoch": T0, "window_end_epoch": T0 + 24 * 3600}


def setup(tmp_path, push=True, **over):
    bare = _origin(tmp_path)
    L = _clone(tmp_path, bare, "a", push=push)
    L.write_json("ops/openai_gate.json", {**GATE24, **over}, "gate")
    if not push:
        L.flush()
    return bare, L


def inbox(L, tmp_path, rows, now):
    p = tmp_path / f"rows{now}.json"
    p.write_text(json.dumps({"results": rows}, ensure_ascii=False))
    mr.cmd_inbox(L, NS(rows=str(p), now=str(now)))


def run(L, now, ai, run_id=None, site=None):
    return mr.cmd_run_openai(L, NS(now=str(now), max_jobs="2", run=run_id), http=ai,
                             fetch=RetryingFetch(site or law_site(), sleep=lambda s: None))


class Counting(FakeOpenAI):
    def __init__(self, **k):
        super().__init__(**k)
        self.requests = 0

    def __call__(self, *a):
        self.requests += 1
        if a[0] == "GET" and a[1].split("/v1")[1].startswith("/agents/sessions?"):
            return 200, {"data": [{"id": sid, "metadata": s.get("metadata", {})} for sid, s in self.sessions.items()]}
        r = super().__call__(*a)
        if a[0] == "POST" and a[1].endswith("/agents/sessions"):
            self.sessions[r[1]["id"]]["metadata"] = a[3]["metadata"]
        return r


def test_idle_hours_make_zero_openai_requests(tmp_path):
    _, L = setup(tmp_path)
    ai = Counting()
    for h in range(3):
        out = run(L, T0 + h * 3600, ai)
        assert out["openai_calls"] == 0
    assert ai.requests == 0                                         # 상태 조회 요청도 없음


def test_only_one_test_directive_runs_and_non_test_is_held(tmp_path):
    _, L = setup(tmp_path)
    inbox(L, tmp_path, [row("r1", "T24-1", "new_task", CHAIN, target="openai_chain"),
                        row("r2", "T24-2", "new_task", CHAIN, target="openai_chain"),
                        row("r3", "R-1", "new_task", CHAIN, target="openai_chain", test=False)], T0)
    ai = Counting()
    out = run(L, T0 + 60, ai)
    st = {r["job"]: r["state"] for r in out["ran"]}
    assert st == {"J-T24-1": "done"}
    jobs = mr._jobs(L.root)
    assert "작업 수 상한" in jobs["J-T24-2"]["state_reason"] and jobs["J-T24-2"]["state"] == "accepted"
    assert "시험 표시" in jobs["J-R-1"]["state_reason"]
    b = json.load(open(os.path.join(L.root, "ops/budget_openai_24h.json")))
    assert len(b["calls"]) == 3 and b["spent_usd"] < 1.0           # 시험 원장은 별도 파일


def test_article_cap_records_dropped(tmp_path):
    _, L = setup(tmp_path)
    inbox(L, tmp_path, [row("r1", "T24-1", "new_task", "조문: 개인정보 보호법 제34조; 개인정보 보호법 제29조; "
                            "개인정보 보호법 제30조", target="openai_chain")], T0)
    run(L, T0 + 60, Counting())
    j = mr._jobs(L.root)["J-T24-1"]
    assert len(j["articles"]) == 2 and j["articles_dropped"] == [["개인정보 보호법", "제30조"]]


def test_total_call_cap_blocks_and_records(tmp_path):
    _, L = setup(tmp_path, max_calls_total=2)
    inbox(L, tmp_path, [row("r1", "T24-1", "new_task", CHAIN, target="openai_chain")], T0)
    out = run(L, T0 + 60, Counting())
    j = mr._jobs(L.root)["J-T24-1"]
    assert j["steps"]["revise"]["status"] == "blocked_budget" and "총 호출 상한" in j["steps"]["revise"]["blocked"]
    assert out["ran"][0]["state"] == "blocked_budget"


def test_window_end_means_no_new_calls(tmp_path):
    _, L = setup(tmp_path)
    inbox(L, tmp_path, [row("r1", "T24-1", "new_task", CHAIN, target="openai_chain")], T0 + 24 * 3600 + 10)
    ai = Counting()
    out = run(L, T0 + 24 * 3600 + 60, ai)
    assert out["openai_calls"] == 0 and ai.requests == 0 and "종료" in out["gate"]


def test_lock_renewed_before_each_paid_step_so_long_job_keeps_lock(tmp_path):
    bare, L = setup(tmp_path)
    inbox(L, tmp_path, [row("r1", "T24-1", "new_task", CHAIN, target="openai_chain")], T0)
    assert L.try_lock("run-A", T0)[0]
    t = [T0 + 40 * 60]

    class Slow(Counting):                                           # 단계마다 30분 걸리는 것으로 모사
        def __call__(self, *a):
            if a[0] == "POST" and (a[1].endswith("/agents/sessions") or a[1].endswith("/events")):
                t[0] += 30 * 60
            return super().__call__(*a)
    ai = Slow()
    # 관리자 시계를 가짜 시계 t로 바꿔 단계마다 시간이 흐르게 함(잠금 50분 < 전체 130분)
    args = NS(now=str(t[0]), max_jobs="1", run="run-A")
    mr._now = (lambda real: (lambda a: float(t[0]) if a.now else real(a)))(mr._now)
    try:
        out = mr.cmd_run_openai(L, args, http=ai, fetch=RetryingFetch(law_site(), sleep=lambda s: None))
    finally:
        import importlib
        importlib.reload(mr)
    assert out["ran"][0]["state"] == "done"                         # 40분+90분 걸렸지만 잠금 유지
    B = _clone(tmp_path, bare, "b", push=True)
    lock = json.load(open(os.path.join(B.root, "ops/manager_lock.json")))
    assert lock["owner"] == "run-A" and lock["expires_at"] > T0 + 50 * 60 and lock.get("renewed_at")


def test_expired_lock_taken_over_stops_old_manager_before_paying(tmp_path):
    bare, A = setup(tmp_path)
    inbox(A, tmp_path, [row("r1", "T24-1", "new_task", CHAIN, target="openai_chain")], T0)
    assert A.try_lock("run-A", T0)[0]
    B = _clone(tmp_path, bare, "b", push=True)
    assert B.try_lock("run-B", T0 + 51 * 60)[0]                   # A가 멈춘 채 만료 → B 인수
    ai = Counting()
    out = run(A, T0 + 52 * 60, ai, run_id="run-A")                 # A가 깨어나 계속하려 함
    assert "잠금 연장 실패" in out["ran"][0]["reason"] and ai.requests == 0


def test_uncertain_create_is_looked_up_before_any_retry(tmp_path):
    P = {"m": Price(1.0, 5.0)}
    b = Budget(str(tmp_path / "b.json"), "openai", Limits(1.0, 20, 1000, 50000), P, clock=lambda: T0)
    ai = Counting()
    b.reserve("J-1:draft:a1", "m", 100)                             # 생성 요청은 갔지만 응답 전에 중단된 상황
    ai("POST", "https://api.openai.com/v1/agents/sessions", {}, {
        "agent": {"instructions": "x"}, "input": [{"content": [{"text": "q"}]}],
        "metadata": {"call": "J-1:draft:a1"}}, 10)
    r = recover_pending(b, ai)
    assert r[0]["action"] == "settled" and r[0]["basis"] == "usage"   # 메타데이터로 찾아 실제 usage 정산
    assert ai.n == 1                                                 # 새 세션을 다시 만들지 않음


def test_schedule_record_flags_trigger_still_enabled_after_end(tmp_path):
    _, L = setup(tmp_path)
    L.write_json("ops/schedule.json", {"trigger_id": "trig_X", "end_at_epoch": T0 + 100}, "s")
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"trigger": {"id": "trig_X", "enabled": True, "next_run_at": "x"}}))
    r = mr.cmd_schedule_record(L, NS(trigger_json=str(p), now=str(T0 + 200)))
    cfg = json.load(open(os.path.join(L.root, "ops/schedule.json")))
    assert r["end_verified"] is False and "자동 재개 없이" in cfg["end_problem"]
    p.write_text(json.dumps({"trigger": {"id": "trig_X", "enabled": False}}))
    assert mr.cmd_schedule_record(L, NS(trigger_json=str(p), now=str(T0 + 300)))["end_verified"] is True
