"""지시함 처리 모의시험 — 실제 지피 지시가 아니다."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.inbox import InboxRow, process, summary  # noqa: E402
from kernel.ledger import Ledger  # noqa: E402
from kernel.model import Stage, Status, Task  # noqa: E402

SCOPES = ["침해사고", "개인정보", "디지털 증거"]


def test_new_goal_in_scope_creates_collect_task_once():
    l, seen = Ledger(), set()
    r = InboxRow("abcd1234efgh", "new_goal", "침해사고 신고 의무의 국제 비교 연구를 시작해")
    (o,) = process([r], l, seen, SCOPES)
    assert o.result == "accepted" and l.get(o.task_ids[0]).stage == Stage.COLLECT
    (o2,) = process([r], l, seen, SCOPES)
    assert o2.result == "duplicate" and len(l.all()) == 1


def test_out_of_scope_goal_goes_to_owner():
    (o,) = process([InboxRow("r2", "new_goal", "부동산 시장 전망 보고서")], Ledger(), set(), SCOPES)
    assert o.result == "needs_owner"


def test_publication_and_cost_need_owner_even_if_gippy_asks():
    l = Ledger()
    outs = process([InboxRow("r3", "new_task", "P1 원고를 학회에 투고해"),
                    InboxRow("r4", "approval_request", "API 크레딧 충전 승인 요청"),
                    InboxRow("r5", "new_task", "결과를 메일 보내 줘")], l, set(), SCOPES)
    assert [o.result for o in outs] == ["needs_owner"] * 3 and l.all() == []


def test_cannot_mark_done_by_instruction():
    (o,) = process([InboxRow("r6", "new_task", "P1:review:v2를 검토 통과로 표시해")], Ledger(), set(), SCOPES)
    assert o.result == "rejected"


def test_retry_rules_blocked_not_bypassed_failed_gets_new_attempt():
    l = Ledger()
    l.add(Task(id="A:collect:v0", project="A", stage=Stage.COLLECT, status=Status.BLOCKED))
    l.add(Task(id="B:collect:v0", project="B", stage=Stage.COLLECT, status=Status.FAILED, attempts=3))
    o1, o2 = process([InboxRow("r7", "retry", "다시 해", target="A:collect:v0"),
                      InboxRow("r8", "retry", "다시 해", target="B:collect:v0")], l, set(), SCOPES)
    assert o1.result == "needs_owner"
    assert o2.result == "accepted" and l.get("B:collect:v0").status == Status.FAILED  # 원 기록 보존
    assert l.get(o2.task_ids[0]).status == Status.QUEUED


def test_summary_lists_raw_states_and_totals():
    l = Ledger()
    l.add(Task(id="X:collect:v0", project="X", stage=Stage.COLLECT, status=Status.DONE))
    l.add(Task(id="X:review:v0", project="X", stage=Stage.REVIEW, status=Status.WAITING_USER))
    s = summary(l, "2026-11-10T05:12:00Z")
    assert s["total"] == 2 and s["counts"] == {"done": 1, "waiting_user": 1}
    assert {r["status"] for r in s["rows"]} == {"done", "waiting_user"} and s["observed_at"]
