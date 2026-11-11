"""공개 상태 변환 모의시험 — 공개 화면으로 나갈 수 있는 값만 나오는지."""
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_directives import CHAIN, FakeOpenAI, gate, inbox, law_site, row  # noqa: E402
from collectors.retry import RetryingFetch  # noqa: E402
from kernel.public_state import assert_lab_state_safe, build  # noqa: E402
from tools import manager_run as mr  # noqa: E402


@pytest.fixture
def L(tmp_path):
    from test_directives import _clone, _origin
    return _clone(tmp_path, _origin(tmp_path), "wd")


def run_chain(L, tmp_path):
    gate(L)
    inbox(L, tmp_path, [row("r1", "G-9", "new_task", CHAIN, target="openai_chain")])
    mr.cmd_run_openai(L, NS(now="1800000100", max_jobs="1", run=None), http=FakeOpenAI(), fetch=RetryingFetch(law_site(), sleep=lambda s: None))


def test_finished_chain_becomes_ordered_public_events(L, tmp_path):
    run_chain(L, tmp_path)
    body = build(L.root, 1800000200)
    kinds = [(e["agentId"], e["type"]) for e in body["events"]]
    assert kinds[0] == ("lead", "accept") and kinds[-1] == ("lead", "complete")
    assert ("research", "handoff") in kinds and ("reviewer", "step_done") in kinds
    assert all(a["state"] == "idle" for a in body["agents"])          # 끝난 작업은 진행 중으로 보이지 않음
    assert body["projects"][0]["title"] == "시험 과제"                   # 지시 내용은 나가지 않음


def test_public_state_contains_no_private_material(L, tmp_path):
    run_chain(L, tmp_path)
    text = json.dumps(build(L.root, 1800000200), ensure_ascii=False)
    for bad in ("sess_", "turn_", "gpt-", "초안: 제34조", "수정본", "조문:", "http", "$", "cost"):
        assert bad not in text, bad


def test_running_step_marks_character_active(L, tmp_path):
    run_chain(L, tmp_path)
    p = os.path.join(L.root, "ledger", "jobs", "J-G-9.json"); j = json.load(open(p))
    j["state"] = "running"; j["steps"]["revise"] = {"status": "started", "attempt": 1, "at": "2026-11-11T01:00:00Z"}
    json.dump(j, open(p, "w"))
    body = build(L.root, 1800000200)
    w = next(a for a in body["agents"] if a["id"] == "writer")
    assert w["state"] == "writing" and w["taskId"] == "G-9" and body["lab"]["status"] == "running"


@pytest.mark.parametrize("field,value", [("summary", "https://example.com"), ("summary", "sess_abc"), ("summary", "x" * 90), ("secret", "y")])
def test_safety_check_rejects(field, value):
    body = {"mode": "live", "revision": 1, "serverTime": "t", "sourceObservedAt": None, "execution": {"enabled": True, "reason": ""},
            "lab": {}, "agents": [{"id": "a", "name": "미나", field: value}], "projects": [], "events": [], "reports": []}
    with pytest.raises(ValueError):
        assert_lab_state_safe(body)
