"""모의시험(simulated). 실제 운영 기록·연구 성과가 아니다."""
import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kernel.ledger import ConflictError, Ledger, TerminalStateError  # noqa: E402
from kernel.manager import LEASE_SECONDS, LIMIT_RECHECK, Manager, review_allowed  # noqa: E402
from kernel.model import Source, SourceStatus, Stage, Status, Task  # noqa: E402
from kernel.projection import Outbox, assert_public_safe, public_projection, viewer_label  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakeExec:
    def __init__(self):
        self.n = itertools.count(1)
        self.state = {}
        self.launched = []
        self.fail_launch = False
        self.lost_response = False     # 세션은 생기지만 응답이 유실되는 경우
        self.tags = {}
        self.find_unknown = False

    def launch(self, task):
        if self.fail_launch:
            raise RuntimeError("503 temporarily unavailable")
        run = f"session_{next(self.n)}"
        self.state[run] = "running"
        self.tags[f"lab-run:{task.id}#a{task.attempts}"] = run
        self.launched.append((task.id, run, dict(task.checkpoint)))
        if self.lost_response:
            raise TimeoutError("timed out waiting for create_session response")
        return run

    def status(self, run):
        return self.state.get(run, "gone")

    def find(self, key):
        if self.find_unknown:
            return "unknown"
        return self.tags.get(key)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    ledger = Ledger(str(tmp_path / "ledger.json"))
    ex = FakeExec()
    return Manager(ledger, ex, clock, max_concurrency=2), ledger, ex, clock


def finish(m, ex, tid, run, result=None, retrieved=True):
    if m.l.get(tid).stage == Stage.COLLECT:
        m.save_source(tid, run, Source("S1", "https://www.law.go.kr/x", "statute",
                                       SourceStatus.RETRIEVED if retrieved else SourceStatus.ACCESS_RESTRICTED))
    ex.state[run] = "idle"
    return m.complete(tid, run, result or {"artifact_hash": "h0"}, event_id=f"{run}:done")


def test_full_pipeline_with_review_revision_and_rereview(env):
    m, l, ex, _ = env
    m.start_project("P")
    order = []
    verdicts = iter(["changes_requested", "approve"])
    for _ in range(20):
        launched = m.tick()
        if not launched:
            break
        for tid, run in launched:
            t = l.get(tid)
            order.append(t.stage.value)
            res = {"artifact_hash": f"h{t.artifact_version}"}
            if t.stage == Stage.REVIEW:
                res["verdict"] = next(verdicts)
                res["changes"] = ["인용 시점 정정"]
            finish(m, ex, tid, run, res)
    assert order == ["collect", "literature", "problem", "design", "draft", "review", "revise", "review"]
    rev = l.get("P:revise:v1")
    assert rev.checkpoint["requested_changes"] == ["인용 시점 정정"]
    ur = l.get("P:user_review:v1")
    assert ur.status == Status.WAITING_USER  # 자동 발행 없음 — 사용자 검토에서 멈춤


def test_partial_source_failure_does_not_lose_others(env):
    m, l, ex, _ = env
    m.start_project("P")
    (tid, run), = m.tick()
    m.save_source(tid, run, Source("S1", "https://www.law.go.kr/a", "statute", SourceStatus.RETRIEVED))
    m.save_source(tid, run, Source("S2", "https://glaw.scourt.go.kr/", "case", SourceStatus.ACCESS_RESTRICTED,
                                   note="connect_unreachable — 우회하지 않음"))
    t = m.complete(tid, run, {"artifact_hash": "h"}, "e1")
    assert t.status == Status.DONE
    assert {s.id: s.status for s in l.sources_of(tid)} == {
        "S1": SourceStatus.RETRIEVED, "S2": SourceStatus.ACCESS_RESTRICTED}


def test_no_retrieved_source_blocks_instead_of_done(env):
    m, l, ex, _ = env
    m.start_project("P")
    (tid, run), = m.tick()
    m.save_source(tid, run, Source("S2", "https://glaw.scourt.go.kr/", "case", SourceStatus.ACCESS_RESTRICTED))
    t = m.complete(tid, run, {}, "e1")
    assert t.status == Status.BLOCKED


def test_interrupted_worker_resumes_from_checkpoint(env):
    m, l, ex, clock = env
    m.start_project("P")
    (tid, run), = m.tick()
    m.heartbeat(tid, run, {"done": [1, 2]})
    ex.state[run] = "gone"                      # 컨테이너 회수·비정상 종료
    clock.t += LEASE_SECONDS + 1
    (tid2, run2), = m.tick()
    assert tid2 == tid and run2 != run
    assert ex.launched[-1][2] == {"done": [1, 2]}   # 저장 지점부터 재개
    assert l.get(tid).attempts == 2
    assert any(e["type"] == "lease_expired" for e in l.events_of(tid))


def test_expired_lease_but_still_running_is_not_duplicated(env):
    m, l, ex, clock = env
    m.start_project("P")
    (tid, run), = m.tick()
    clock.t += LEASE_SECONDS + 1
    assert m.tick() == []                     # 실제로 실행 중이면 재배정하지 않음
    assert len(ex.launched) == 1


def test_duplicate_and_late_completion(env):
    m, l, ex, clock = env
    m.start_project("P")
    (tid, run), = m.tick()
    ex.state[run] = "gone"
    clock.t += LEASE_SECONDS + 1
    (_, run2), = m.tick()
    # 늦게 도착한 옛 실행의 완료 통지는 채택되지 않음
    m.save_source(tid, run2, Source("S1", "u", "statute", SourceStatus.RETRIEVED))
    t = m.complete(tid, run, {"artifact_hash": "old"}, "old:done")
    assert t.status == Status.RUNNING
    t = m.complete(tid, run2, {"artifact_hash": "new"}, "new:done")
    t2 = m.complete(tid, run2, {"artifact_hash": "new"}, "new:done")  # 재전송
    assert t.status == t2.status == Status.DONE
    assert len([x for x in l.all() if x.stage == Stage.LITERATURE]) == 1  # 다음 과제 1개만


def test_cas_conflict_two_claimers():
    l = Ledger()
    l.add(Task(id="T", project="P", stage=Stage.COLLECT))
    t = l.get("T")
    l.update("T", t.rev, status=Status.RUNNING, lease_owner="a")
    with pytest.raises(ConflictError):
        l.update("T", t.rev, status=Status.RUNNING, lease_owner="b")


def test_terminal_state_cannot_regress():
    l = Ledger()
    l.add(Task(id="T", project="P", stage=Stage.COLLECT, status=Status.DONE))
    with pytest.raises(TerminalStateError):
        l.update("T", 0, status=Status.RUNNING)


def test_usage_limit_waits_without_consuming_attempts(env):
    m, l, ex, clock = env
    m.start_project("P")
    (tid, run), = m.tick()
    t = m.fail(tid, run, "Claude usage limit reached; resets later")
    assert t.status == Status.WAITING_LIMIT and t.attempts == 0
    assert m.tick() == []
    clock.t += LIMIT_RECHECK + 1
    assert [x[0] for x in m.tick()] == [tid]


def test_blocked_task_does_not_stop_independent_project(env):
    m, l, ex, _ = env
    m.start_project("A")
    m.start_project("B")
    launched = dict(m.tick())
    m.fail("A:collect:v0", launched["A:collect:v0"], "HTTP 403 forbidden (login required)")
    finish(m, ex, "B:collect:v0", launched["B:collect:v0"])
    nxt = [tid for tid, _ in m.tick()]
    assert l.get("A:collect:v0").status == Status.BLOCKED
    assert nxt == ["B:literature:v0"]


def test_transient_retry_is_bounded(env):
    m, l, ex, clock = env
    m.start_project("P")
    for i in range(3):
        clock.t += 3600
        (tid, run), = m.tick()
        t = m.fail(tid, run, "connection reset by peer")
    assert t.status == Status.FAILED and t.attempts == 3


def test_launch_failure_confirmed_absent_requeues_only_after_lease(env):
    m, l, ex, clock = env
    m.start_project("P")
    ex.fail_launch = True
    assert m.tick() == []
    t = l.get("P:collect:v0")
    assert t.status == Status.RUNNING and t.lease_owner == "launching"   # 바로 재대기하지 않음
    ex.fail_launch = False
    clock.t += 61
    assert m.tick() == [] and len(ex.launched) == 0                       # 임대 유지 중엔 재실행 없음
    clock.t += LEASE_SECONDS
    m.tick()                                    # 없음이 확인되고 임대도 만료 → 그때 재배정
    assert len(ex.launched) == 1


def test_lost_launch_response_adopts_existing_session_no_duplicate(env):
    m, l, ex, clock = env
    m.start_project("P")
    ex.lost_response = True
    m.tick()
    assert len(ex.launched) == 1 and l.get("P:collect:v0").lease_owner == "launching"
    ex.lost_response = False
    clock.t += LEASE_SECONDS + 1
    m.tick()
    t = l.get("P:collect:v0")
    assert t.lease_owner == ex.launched[0][1] and len(ex.launched) == 1   # 실제 세션을 찾아 채택


def test_unknown_status_never_requeues_and_escalates(env):
    m, l, ex, clock = env
    m.start_project("P")
    (tid, run), = m.tick()
    ex.state[run] = "unknown"
    for _ in range(3):
        clock.t += LEASE_SECONDS + 1
        m.tick()
    t = l.get(tid)
    assert len(ex.launched) == 1                       # 중복 실행 없음
    assert t.status == Status.WAITING_USER and "확인 불가" in t.error_detail


def test_find_unknown_does_not_relaunch(env):
    m, l, ex, clock = env
    m.start_project("P")
    ex.lost_response = True
    m.tick()
    ex.lost_response, ex.find_unknown = False, True
    for _ in range(3):
        clock.t += LEASE_SECONDS + 1
        m.tick()
    assert len(ex.launched) == 1 and l.get("P:collect:v0").status == Status.WAITING_USER


def test_review_independence_rules():
    assert review_allowed("ctx-r", {"ctx-w"}, "h1", "h1")[0]
    assert not review_allowed("ctx-w", {"ctx-w"}, "h1", "h1")[0]
    assert not review_allowed("ctx-r", {"ctx-w"}, "h1", "h2")[0]


def test_public_projection_excludes_private_and_test(env):
    m, l, ex, clock = env
    l.add(Task(id="secret-question:collect:v0", project="개인정보 유출 원고", stage=Stage.COLLECT, test=False))
    l.add(Task(id="T:collect:v0", project="T", stage=Stage.COLLECT, test=True))
    m.tick()
    p = public_projection(l, clock())
    assert_public_safe(p)
    dump = str(p)
    assert "secret" not in dump and "원고" not in dump and "session_" not in dump
    assert p["counts"] == {"running": 1}
    with pytest.raises(ValueError):
        assert_public_safe({**p, "title": "x"})


def test_outbox_retries_and_viewer_marks_stale(env):
    m, l, ex, clock = env
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IOError("502")

    ob = Outbox(flaky)
    p = public_projection(l, clock())
    ob.publish(p)
    assert ob.pending and ob.log[-1][1].startswith("failed")
    ob.flush()
    assert not ob.pending and ob.log[-1][1] == "delivered"
    ob.publish(p)  # 동일 내용 — 재전송 생략
    assert calls["n"] == 2
    assert viewer_label(p, clock() + 16 * 60).startswith("확인 지연")


def test_ledger_survives_restart(tmp_path):
    path = str(tmp_path / "l.json")
    l1 = Ledger(path)
    l1.add(Task(id="T", project="P", stage=Stage.COLLECT, checkpoint={"done": ["S1"]}))
    l1.record("e1", task="T", type="x")
    l2 = Ledger(path)   # 프로세스 재시작 모사
    assert l2.get("T").checkpoint == {"done": ["S1"]}
    assert not l2.record("e1", task="T", type="x")


# ---- 실제 시험(2026-11-10)에서 발견한 사례를 회귀 시험으로 고정 ----
from kernel.manager import classify  # noqa: E402
from kernel.model import ErrorClass, judge_retrieval  # noqa: E402


def test_external_source_rate_limit_is_not_subscription_limit():
    assert classify('OpenAlex "Rate limit exceeded"') == ErrorClass.TRANSIENT
    assert classify("source_rate_limit: crossref 429") == ErrorClass.TRANSIENT
    assert classify("Claude usage limit reached") == ErrorClass.LIMIT


def test_shell_page_is_not_retrieved_source():
    shell = b"<html><title>\xea\xb0\x9c\xec\x9d\xb8\xec\xa0\x95\xeb\xb3\xb4\xeb\xb3\xb4\xed\x98\xb8\xeb\xb2\x95</title><frameset/></html>" + b" " * 1100
    assert judge_retrieval(200, shell, ["제34조"]) == SourceStatus.SHELL_ONLY
    full = ("제34조(개인정보 유출등의 통지ㆍ신고) " * 200).encode()
    assert judge_retrieval(200, full, ["제34조"]) == SourceStatus.RETRIEVED
    assert judge_retrieval(0, b"", []) == SourceStatus.ACCESS_RESTRICTED
