"""연구소 v2 관리자 실행기 — 예약 실행 세션(Claude)이 단계별로 호출한다.

세션 생성·상태 조회는 MCP 도구로만 가능하므로 2단계로 나눈다.
  1) plan-observe  : 상태를 물어봐야 할 실행(세션ID)·찾아야 할 실행 키 목록을 출력
  2) (관리자가 get_session / list_sessions(tags) 로 조회해 obs.json 작성)
  3) tick          : 결과 수집 → 임대 회수(확인된 종료만) → 다음 단계 생성 → 배정(실행은 '보류'로 남김)
  4) (관리자가 to_launch 마다 create_session(tags=[launch_key]) 실행)
  5) adopt         : 생성된 세션ID를 원장에 연결(응답 유실 시 다음 주기 find 로 자동 연결)
  6) summary       : status/ 갱신, 일괄 푸시
연구원은 원장(ledger/)을 직접 바꾸지 않는다. results/<과제>/<실행키>.json 만 쓴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.inbox import summary as inbox_summary  # noqa: E402
from kernel.ledger import ConflictError  # noqa: E402
from kernel.manager import Manager, launch_key  # noqa: E402
from kernel.model import Source, SourceStatus, Status  # noqa: E402
from kernel.repo_ledger import RepoLedger, _safe  # noqa: E402


class Deferred(Exception):
    """실행은 MCP로 하므로 여기서는 생성하지 않는다 — 'launching' 임대로 남김."""


class ObsExecutor:
    def __init__(self, obs: dict):
        self.obs = obs
        self.to_launch: list[dict] = []

    def launch(self, task):
        self.to_launch.append({"task_id": task.id, "launch_key": launch_key(task), "stage": task.stage.value,
                               "project": task.project, "test": task.test})
        raise Deferred("deferred: manager session will create it")

    def status(self, run_id):
        return self.obs.get("status", {}).get(run_id, "unknown")

    def find(self, key):
        f = self.obs.get("find", {})
        return f[key] if key in f else "unknown"


def _now(args) -> float:
    return float(args.now) if args.now else time.time()


def _cfg(root: str) -> dict:
    p = os.path.join(root, "ops", "schedule.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def cmd_plan_observe(L: RepoLedger, args) -> dict:
    runs, keys = [], []
    for t in L.all():
        if t.status != Status.RUNNING:
            continue
        if t.lease_owner == "launching":
            keys.append(t.checkpoint.get("launch_key", launch_key(t)))
        elif t.lease_owner:
            runs.append(t.lease_owner)
    cfg = _cfg(L.root)
    now = _now(args)
    expired = bool(cfg.get("end_at_epoch")) and now >= cfg["end_at_epoch"]
    return {"check_status": sorted(set(runs)), "find_keys": sorted(set(keys)),
            "schedule_expired": expired, "trigger_id": cfg.get("trigger_id")}


def _ingest_results(L: RepoLedger, m: Manager) -> list[dict]:
    base = os.path.join(L.root, "results")
    done = []
    if not os.path.isdir(base):
        return done
    for d, _, files in os.walk(base):
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            r = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            tid, run, key = r.get("task_id"), r.get("run"), r.get("launch_key")
            evt = f"result:{key}"
            try:
                t = L.get(tid)
            except FileNotFoundError:
                continue
            if t.status != Status.RUNNING:
                continue  # 이미 처리됨 또는 늦은 결과 — complete()가 기록
            if t.lease_owner == "launching" and t.checkpoint.get("launch_key") == key:
                L.update(tid, t.rev, lease_owner=run)  # 결과가 생성 확인보다 먼저 도착
                t = L.get(tid)
            for s in r.get("sources", []):
                try:
                    m.save_source(tid, t.lease_owner, Source(**{**s, "status": SourceStatus(s["status"])}))
                except ConflictError:
                    pass
            if r.get("outcome") == "done":
                nt = m.complete(tid, run, r.get("result", {}), evt)
            else:
                try:
                    nt = m.fail(tid, run, r.get("error", "fatal: no error text"))
                except ConflictError:
                    L.record(f"{evt}:stale", task=tid, type="late_result_rejected", run=run)
                    continue
            done.append({"task": tid, "outcome": r.get("outcome"), "status": nt.status.value})
    return done


def cmd_tick(L: RepoLedger, args) -> dict:
    obs = json.load(open(args.obs, encoding="utf-8")) if args.obs else {}
    now = _now(args)
    if obs.get("rate_limit") == "limited" or obs.get("is_using_overage"):
        L.record(f"tick:{int(now)}:limit", type="waiting_limit", at=now,
                 overage=bool(obs.get("is_using_overage")))
        return {"action": "stop", "reason": "usage limited or overage in use — no new work",
                "disable_trigger": bool(obs.get("is_using_overage"))}
    ex = ObsExecutor(obs)
    m = Manager(L, ex, clock=lambda: now, max_concurrency=int(_cfg(L.root).get("max_concurrency", 2)))
    ingested = _ingest_results(L, m)
    m.tick()
    return {"action": "continue", "ingested": ingested, "to_launch": ex.to_launch}


def cmd_adopt(L: RepoLedger, args) -> dict:
    for t in L.all():
        if t.status == Status.RUNNING and t.lease_owner == "launching" and \
                t.checkpoint.get("launch_key") == args.key:
            L.update(t.id, t.rev, lease_owner=args.session)
            L.record(f"{args.key}:adopted", task=t.id, type="launch", run=args.session)
            return {"adopted": t.id}
    return {"adopted": None, "note": "해당 키의 launching 과제 없음(이미 연결됐거나 없음)"}


def cmd_summary(L: RepoLedger, args) -> dict:
    now = _now(args)
    iso = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class _Mem:
        def all(self_inner):
            return L.all()
    s = inbox_summary(_Mem(), iso)
    obs = json.load(open(args.obs, encoding="utf-8")) if args.obs else {}
    s["operations"] = {"manager_run": args.run or "unknown", "usage_signal": obs.get("usage_signal", "미확인"),
                       "is_using_overage": obs.get("is_using_overage"), "schedule": _cfg(L.root)}
    L.write_json("status/summary.json", s, f"status: summary {iso}")
    L.record(f"tick:{args.run or int(now)}:summary", type="manager_run", run=args.run, at=iso,
             counts=s["counts"])
    if args.push:
        L.flush()
    return {"observed_at": iso, "counts": s["counts"], "total": s["total"]}


def worker_prompt(task_id: str, key: str, stage: str, brief: str, repo: str = "gx999/daeyong-lab") -> str:
    safe_t, safe_k = _safe(task_id), _safe(key)
    return f"""당신은 '정대용 디지털 연구소 v2'의 연구원입니다. 과제 {task_id} (단계 {stage}), 실행키 {key}.
web1 저장소의 규칙 문서(lab-v2/prompts/common-rules.md, stages.md의 {stage} 절)를 따르되, 이 세션의 작업 저장소는 비공개 {repo} 입니다.
금지: ledger/·status/·drafts/ 등 다른 경로 수정, 원고 공개, 외부 전송, 예약 생성, 결제·인증 변경, 접근 제한 우회.
과제 내용: {brief}
끝나면(성공·실패 모두) 결과를 results/{safe_t}/{safe_k}.json 한 파일로만 쓰고 커밋·푸시하세요(main, 충돌 시 git pull --rebase 후 재시도 최대 3회):
{{"task_id":"{task_id}","launch_key":"{key}","run":"<mcp get_session 의 세션ID>","outcome":"done|fail",
 "result":{{...단계별 산출(초안이면 draft_sha256·length·draft 전문, 검토면 verdict·changes·claims)...}},
 "sources":[{{"id":..,"url":..,"kind":..,"status":"retrieved|search_only|shell_only|access_restricted|transient_failed","checked_at":"<date -u 실측>","doc_date":..,"date_kind":..,"sha256":..,"note":..}}],
 "error":"<실패 시: transient:/access:/permission:/usage_limit:/fatal: 접두어 + 원문 오류>","at":"<date -u 실측>"}}
일부 출처가 실패해도 확보한 것은 sources에 남기고 계속하세요. 시각은 실측값만 쓰세요."""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan-observe", "tick", "adopt", "summary", "prompt"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--obs")
    ap.add_argument("--now")
    ap.add_argument("--key")
    ap.add_argument("--session")
    ap.add_argument("--run")
    ap.add_argument("--task")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    L = RepoLedger(a.repo, push=False)
    if a.cmd == "prompt":
        t = L.get(a.task)
        out = {"prompt": worker_prompt(t.id, t.checkpoint.get("launch_key", launch_key(t)), t.stage.value,
                                       json.dumps(t.checkpoint.get("brief", ""), ensure_ascii=False))}
    else:
        out = {"plan-observe": cmd_plan_observe, "tick": cmd_tick, "adopt": cmd_adopt,
               "summary": cmd_summary}[a.cmd](L, a)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
