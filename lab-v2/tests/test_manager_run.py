"""관리자 실행기 종단 모의시험 — 로컬 git 원장 + 가짜 연구원 결과 파일. 실제 운영 기록이 아니다."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.model import Stage, Status, Task  # noqa: E402
from kernel.repo_ledger import RepoLedger, _safe  # noqa: E402
from tools import manager_run as mr  # noqa: E402


def _repo(tmp_path):
    bare, wd = tmp_path / "o.git", tmp_path / "wd"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(wd)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wd), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(wd), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
    return RepoLedger(str(wd), push=False)


def _obs(tmp_path, **o):
    p = tmp_path / "obs.json"
    p.write_text(json.dumps(o))
    return str(p)


def _result(L, launch, run, outcome, result=None, sources=None, error=""):
    p = os.path.join(L.root, "results", _safe(launch["task_id"]), _safe(launch["launch_key"]) + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"task_id": launch["task_id"], "launch_key": launch["launch_key"], "run": run, "outcome": outcome,
               "result": result or {}, "sources": sources or [], "error": error}, open(p, "w"))


SRC_OK = [{"id": "S1", "url": "https://www.law.go.kr/x", "kind": "statute", "status": "retrieved"},
          {"id": "S2", "url": "https://glaw.scourt.go.kr/", "kind": "case", "status": "access_restricted"}]


def test_end_to_end_chain_with_independent_failure(tmp_path):
    L = _repo(tmp_path)
    L.add(Task(id="T24A:collect:v0", project="T24A", stage=Stage.COLLECT, test=True))
    L.add(Task(id="T24B:collect:v0", project="T24B", stage=Stage.COLLECT, test=True))
    now = 1_000_000.0
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path), now=now))
    launches = {x["task_id"]: x for x in out["to_launch"]}
    assert set(launches) == {"T24A:collect:v0", "T24B:collect:v0"}          # 2개 동시 배정
    assert all(L.get(t).lease_owner == "launching" for t in launches)       # 생성 전엔 '보류'
    mr.cmd_adopt(L, NS(key=launches["T24A:collect:v0"]["launch_key"], session="sA"))
    mr.cmd_adopt(L, NS(key=launches["T24B:collect:v0"]["launch_key"], session="sB"))
    # A: 한 출처 접근 제한이지만 하나는 확보 → done / B: 접근 제한으로 실패
    _result(L, launches["T24A:collect:v0"], "sA", "done", {"artifact_hash": "h0"}, SRC_OK)
    _result(L, launches["T24B:collect:v0"], "sB", "fail", error="access: 403 login required")
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path, status={"sA": "idle", "sB": "idle"}), now=now + 60))
    assert L.get("T24A:collect:v0").status == Status.DONE
    assert L.get("T24B:collect:v0").status == Status.BLOCKED                # 우회 없이 보류
    assert {s.id: s.status.value for s in L.sources_of("T24A:collect:v0")} == {
        "S1": "retrieved", "S2": "access_restricted"}                       # 확보 자료 보존
    nxt = [x["task_id"] for x in out["to_launch"]]
    assert nxt == ["T24A:literature:v0"]                                    # 막히지 않은 과제는 계속


def test_unknown_session_is_not_relaunched_by_runner(tmp_path):
    L = _repo(tmp_path)
    L.add(Task(id="T:collect:v0", project="T", stage=Stage.COLLECT, test=True))
    now = 2_000_000.0
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path), now=now))
    key = out["to_launch"][0]["launch_key"]
    # 관리자가 세션을 만들었지만 adopt 전에 실행이 끊김 → 다음 주기 find 조회 실패(unknown)
    for i in range(1, 4):
        out = mr.cmd_tick(L, NS(obs=_obs(tmp_path, find={}), now=now + i * 3600))
        assert out["to_launch"] == []                                       # 재생성 없음
    assert L.get("T:collect:v0").status == Status.WAITING_USER


def test_lost_adopt_recovered_by_find(tmp_path):
    L = _repo(tmp_path)
    L.add(Task(id="T:collect:v0", project="T", stage=Stage.COLLECT, test=True))
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path), now=3_000_000.0))
    key = out["to_launch"][0]["launch_key"]
    plan = mr.cmd_plan_observe(L, NS(now=3_000_100.0))
    assert plan["find_keys"] == [key]
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path, find={key: "sX"}), now=3_000_100.0))
    assert L.get("T:collect:v0").lease_owner == "sX" and out["to_launch"] == []


def test_overage_or_limit_stops_without_new_work(tmp_path):
    L = _repo(tmp_path)
    L.add(Task(id="T:collect:v0", project="T", stage=Stage.COLLECT, test=True))
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path, is_using_overage=True), now=4e6))
    assert out["action"] == "stop" and out["disable_trigger"] is True
    assert L.get("T:collect:v0").status == Status.QUEUED
    out = mr.cmd_tick(L, NS(obs=_obs(tmp_path, rate_limit="limited"), now=4e6 + 1))
    assert out["action"] == "stop" and out["disable_trigger"] is False


def test_schedule_expiry_flag(tmp_path):
    L = _repo(tmp_path)
    L.write_json("ops/schedule.json", {"end_at_epoch": 5e6, "trigger_id": "trig_x"}, "cfg")
    assert mr.cmd_plan_observe(L, NS(now=5e6 - 1))["schedule_expired"] is False
    assert mr.cmd_plan_observe(L, NS(now=5e6))["schedule_expired"] is True
