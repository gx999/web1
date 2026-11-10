"""24시간 무인 시험 모의 — 매시 관리자 실행 24회+α를 가짜 시계로 재현. 실제 예약·실제 API 호출 없음.

검증: 잠금(이중 실행 방지), 중단된 실행 뒤 다음 주기 인수, 지시 중복 방지, 단계 연결, 재시작 복구,
종료 시각 이후 '예약 끄기' 신호, 예약 존재 ≠ 연구 완료(상태는 실행 기록으로만 바뀜)."""
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_directives import CHAIN, FakeOpenAI, _clone, _origin, gate, law_site, row  # noqa: E402
from collectors.retry import RetryingFetch  # noqa: E402
from tools import manager_run as mr  # noqa: E402

T0 = 1_800_000_000          # 시험 시작(가짜 시각)
H = 3600


def one_run(L, tmp_path, hour, rows, ai, crash=False):
    """예약 실행 1회 = 문서 11의 실행 순서."""
    now = str(T0 + hour * H)
    run = f"run-h{hour}"
    lk = mr.cmd_lock(L, NS(run=run, now=now))
    if not lk["acquired"]:
        return {"skipped": True, "held_by": lk["held_by"]}
    po = mr.cmd_plan_observe(L, NS(now=now))
    if po["schedule_expired"]:
        mr.cmd_summary(L, NS(now=now, obs=None, run=run, push=True))
        mr.cmd_unlock(L, NS(run=run, now=now))
        L.flush()
        return {"expired": True, "disable_trigger": po["trigger_id"]}
    p = tmp_path / f"rows{hour}.json"
    p.write_text(json.dumps({"results": rows}, ensure_ascii=False))
    mr.cmd_inbox(L, NS(rows=str(p), now=now))
    ai.crash_on = "review" if crash else None
    try:
        out = mr.cmd_run_openai(L, NS(now=now, max_jobs="2"), http=ai,
                                fetch=RetryingFetch(law_site(), sleep=lambda s: None))
    except KeyboardInterrupt:
        L.flush()                     # 중단 직전까지의 커밋은 남는다고 가정(최악: 커밋 일부 유실 → 예약 원장 기준 복구)
        return {"crashed": True}      # 잠금 해제 없이 종료
    mr.cmd_summary(L, NS(now=now, obs=None, run=run, push=False))
    mr.cmd_unlock(L, NS(run=run, now=now))
    L.flush()
    return {"ran": out["ran"], "recovered": out.get("recovered")}


@pytest.fixture
def L(tmp_path):
    L = _clone(tmp_path, _origin(tmp_path), "mgr", push=False)
    L.write_json("ops/schedule.json", {"name": "연구소v2 24시간 무인시험", "trigger_id": "trig_TEST",
                                       "start_at_epoch": T0, "end_at_epoch": T0 + 24 * H, "max_concurrency": 2}, "schedule")
    gate(L)
    L.flush()
    return L


def test_24_hourly_runs(L, tmp_path):
    ai = FakeOpenAI()
    rows: list = []
    log = {}
    for h in range(0, 27):
        if h == 3:
            rows = [row("r1", "T24-1", "new_task", CHAIN, target="openai_chain")]            # 지피가 시험 지시 작성
        if h == 5:
            rows = rows + [row("r2", "T24-2", "new_task", "조문: 개인정보 보호법 제34조", target="openai_chain")]
        if h == 9:
            rows = rows + [row("r3", "T24-1", "question", "같은 요청ID 재사용")]                 # 잘못된 중복 ID
        log[h] = one_run(L, tmp_path, h, rows, ai, crash=(h == 5))
        if h == 5:   # 같은 시각에 다른 관리자(수동 실행 등)가 끼어들면 잠금에 막힘
            other = mr.cmd_lock(L, NS(run="run-intruder", now=str(T0 + 5 * H + 60)))
            assert not other["acquired"]
    idx = json.load(open(os.path.join(L.root, "status/directives/index.json"), encoding="utf-8"))
    st = {r["directive_id"]: r["state"] for r in idx["rows"]}
    assert st["T24-1"] == "done" and st["T24-2"] == "done"
    assert log[5] == {"crashed": True}
    assert log[6].get("recovered")                                   # 다음 주기에 중단 호출 정산
    assert any(x["action"] == "settled" for x in log[6]["recovered"])
    assert log[24]["expired"] and log[24]["disable_trigger"] == "trig_TEST"
    assert log[25]["expired"] and log[26]["expired"]                 # 끄기 실패해도 매번 아무 작업 없이 종료
    b = json.load(open(os.path.join(L.root, "ops/budget_openai_agents.json")))
    ids = [c["id"] for c in b["calls"]]
    assert len(ids) == len(set(ids)) and not b["reserved"] and not b["pending"]
    # 지시당 세션 생성은 초안 1 + 검토(재시도 포함) — 같은 단계가 같은 시도 ID로 두 번 호출되지 않음
    assert sorted(i.split(":")[1] for i in ids if i.startswith("J-T24-1")) == ["draft", "review", "revise"]
    s = json.load(open(os.path.join(L.root, "status/summary.json"), encoding="utf-8"))
    assert s["operations"]["unattended_verified"] is False           # 모의 성공을 무인 검증 완료로 기록하지 않음
    dup = [f for f in os.listdir(os.path.join(L.root, "status/directives")) if f.startswith("_dup_")]
    assert dup


def test_schedule_without_directives_records_runs_but_no_research(L, tmp_path):
    ai = FakeOpenAI()
    for h in range(3):
        one_run(L, tmp_path, h, [], ai)
    s = json.load(open(os.path.join(L.root, "status/summary.json"), encoding="utf-8"))
    assert s["directives"]["total"] == 0 and s["openai_jobs"] == [] and ai.prompts == []
    ev = open(os.path.join(L.root, "ledger/events", sorted(os.listdir(os.path.join(L.root, "ledger/events")))[0]),
              encoding="utf-8").read()
    assert ev.count('"manager_run"') == 3                            # 실행 사실만 기록
