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
from kernel import directives as dv  # noqa: E402

APPROVED_SCOPES = ["침해사고", "개인정보", "디지털 증거", "형사절차", "정보통신망", "사이버"]
ARTICLE_RE = __import__("re").compile(r"\s*(.+?)\s+(제\d+조(?:의\d+)?)\s*$")


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


def _read(root: str, rel: str, default=None):
    p = os.path.join(root, rel)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def _tests(root: str) -> list[dict]:
    """tests/ 아래 시험 실행 기록(시험용 — 연구 성과 아님)."""
    out = []
    calls = {c["id"]: c for c in (_read(root, "ops/budget_openai_agents.json") or {}).get("calls", [])}
    corr = _read(root, "tests/agents_pilot/CORRECTIONS.json", {})
    name = {"draft": "draft_v0", "review": "review_v0", "revise": "draft_v1"}
    base = os.path.join(root, "tests", "agents_pilot")
    for rid in sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []:
        r = _read(root, f"tests/agents_pilot/{rid}/run.json", {})
        steps = [x for x in r.get("steps", []) if x.get("stage") != "collect"]
        src = next((x["sources"] for x in r.get("steps", []) if x.get("stage") == "collect"), [])
        out.append({"run_id": rid, "test": True, "kind": "openai_agents_pilot", "result": r.get("result"),
                    "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
                    "steps": [{"stage": x["stage"], "model": x.get("model"), "ok": x.get("ok"),
                               "session_id": x.get("session_id"),
                               "cost_usd": calls.get(f"{rid}:{name.get(x['stage'])}", {}).get("cost", x.get("cost")),
                               "basis": calls.get(f"{rid}:{name.get(x['stage'])}", {}).get("basis", x.get("basis"))}
                              for x in steps],
                    "corrections": corr.get(rid, []),
                    "sources": {x["article"]: x["status"] for x in src},
                    "path": f"tests/agents_pilot/{rid}/"})
    return out


def cmd_summary(L: RepoLedger, args) -> dict:
    now = _now(args)
    iso = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class _Mem:
        def all(self_inner):
            return L.all()
    s = inbox_summary(_Mem(), iso)
    prev = _read(L.root, "status/summary.json", {})
    obs = json.load(open(args.obs, encoding="utf-8")) if args.obs else {}
    s["writer"] = "claude_manager"
    s["note"] = ("rows/counts = v2 원장 과제의 원본 상태값. 지시(directives)·OpenAI 작업(openai_jobs)·시험(tests)·"
                 "외부 관측(external_observed)은 별개 항목이며 서로 합산하지 않음")
    s["projects"] = prev.get("projects", {})
    s["directives"] = {k: v for k, v in (_read(L.root, "status/directives/index.json") or
                                         {"total": 0, "counts": {}, "rows": []}).items() if k != "writer"}
    s["openai_jobs"] = [{"job": j["id"], "directive_id": j.get("directive_id"), "state": j.get("state"),
                         "reason": j.get("state_reason", ""), "test": j.get("test")} for j in _jobs(L.root).values()]
    s["tests"] = _tests(L.root)
    s["external_observed"] = _read(L.root, "ops/external_observations.json", [])
    b = _read(L.root, "ops/budget_openai_agents.json")
    s["budgets"] = {"openai_agents": None if b is None else
                    {"spent_usd_internal": b["spent_usd"], "reserved": b["reserved"], "killed": b["killed"],
                     "calls": len(b["calls"]), "note": "내부 집계(토큰×공식 요금, 캐시 입력도 정가). 청구액 아님"},
                    "openai_gate": _openai_gate(L.root)[1]}
    last_obs = ((_cfg(L.root) or {}).get("trigger_observations") or [None])[-1]
    s["operations_trigger_last_observed"] = last_obs
    s["operations"] = {"manager_run": args.run or "unknown", "last_ok_run_at": iso, "usage_signal": obs.get("usage_signal", "미확인"),
                       "is_using_overage": obs.get("is_using_overage"), "schedule": _cfg(L.root) or "없음",
                       "unattended_verified": prev.get("operations", {}).get("unattended_verified", False)}
    L.write_json("status/summary.json", s, f"status: summary {iso}")
    L.record(f"tick:{args.run or int(now)}:summary", type="manager_run", run=args.run, at=iso,
             counts=s["counts"])
    if args.push:
        L.flush()
    return {"observed_at": iso, "counts": s["counts"], "total": s["total"],
            "directives": s["directives"].get("counts"), "tests": len(s["tests"])}


# ---- 지시함·OpenAI 연구원 작업 ------------------------------------------------
def _jobs(root: str) -> dict[str, dict]:
    d = os.path.join(root, "ledger", "jobs")
    if not os.path.isdir(d):
        return {}
    return {f[:-5]: json.load(open(os.path.join(d, f), encoding="utf-8")) for f in sorted(os.listdir(d))
            if f.endswith(".json")}


def parse_articles(content: str) -> list[list[str]]:
    """지시 내용의 '조문: 법령명 제N조; 법령명 제N조의M' 줄을 읽는다(없으면 빈 목록 → blocked)."""
    for line in content.splitlines():
        if line.strip().startswith("조문:"):
            out = []
            for part in line.split(":", 1)[1].split(";"):
                m = ARTICLE_RE.match(part)
                if m:
                    out.append([m.group(1).strip(), m.group(2)])
            return out
    return []


def cmd_inbox(L: RepoLedger, args) -> dict:
    """관리자 세션이 Notion 조회 결과(rows 모드 JSON)를 파일로 넘긴다. Notion에는 쓰지 않는다."""
    now = _iso(args)
    rows = json.load(open(args.rows, encoding="utf-8"))
    rows = rows.get("results", rows) if isinstance(rows, dict) else rows
    store = dv.DirectiveStore(L.root, L.write_json)

    def new_job(d: dict, row: dict) -> str:
        jid = "J-" + _safe(d["directive_id"])
        L.write_json(f"ledger/jobs/{jid}.json",
                     {"id": jid, "directive_id": d["directive_id"], "test": d["test"], "question": d["content"],
                      "articles": parse_articles(d["content"]), "state": "accepted", "steps": {},
                      "created_at": now}, f"job: {jid} created")
        return jid
    known = {d["notion_row"] for d in store.all()}
    migrated = []
    for r in rows:                     # 이전 형식(inbox_seen/)으로 이미 처리된 행은 다시 실행하지 않고 기록만 이관
        rid = dv.row_id(r)
        old = _read(L.root, f"inbox_seen/{rid}.json")
        if old and rid not in known and r.get("요청ID"):
            rec = {"directive_id": r["요청ID"], "notion_row": rid, "notion_url": r.get("url"), "kind": r.get("종류"),
                   "content": (r.get("내용") or "")[:2000], "target": r.get("대상") or "", "author": r.get("작성자"),
                   "test": r.get("시험") == "__YES__", "notion_created": r.get("작성 시각"),
                   "content_sha256": dv.content_sha(r), "received_at": old.get("processed_at"), "updated_at": now,
                   "state": old.get("result", "answered"), "state_reason": old.get("reason", ""),
                   "history": [{"at": old.get("processed_at"), "state": old.get("result"),
                                "reason": "이전 처리 기록(inbox_seen/)에서 이관 — 재실행 안 함"}],
                   "tasks": [], "jobs": [], "evidence": [],
                   "results": [{"kind": "answer", "path": old.get("answer_ref")}] if old.get("answer_ref") else []}
            store.put(rec)
            migrated.append(rid)
    res = dv.intake(rows, store, L, APPROVED_SCOPES, now, on_openai_job=new_job)
    store.write_index(now)
    return {"observed_at": now, "migrated": migrated, "processed": res}


GATE_DEFAULTS = {   # 24시간 시험 제안값(사용자 확인 후 ops/openai_gate.json에 기록)
    "budget_file": "ops/budget_openai_agents.json", "budget_usd": None, "max_calls_total": 0,
    "max_calls_per_day": None, "max_concurrency": None, "max_retries": None,
    "max_jobs_total": 0, "require_test_directive": False, "max_articles": 0,
    "window_start_epoch": None, "window_end_epoch": None}


class LockLost(Exception):
    """잠금 연장 실패 — 다른 관리자가 인수했을 수 있으므로 유료 호출을 하지 않고 중단."""


def _gate_cfg(root: str) -> dict:
    g = _read(root, "ops/openai_gate.json", {}) or {}
    return {**GATE_DEFAULTS, **{k: v for k, v in g.items() if v is not None}}


def _openai_gate(root: str) -> tuple[bool, str]:
    """ops/openai_gate.json: 사용자 확인 항목이 모두 기록돼야 유료 호출 허용. 파일 자체가 없으면 차단.
    확인 항목 값은 사용자가 화면에서 본 값을 그대로 적는다(추정 금지)."""
    p = os.path.join(root, "ops", "openai_gate.json")
    if not os.path.exists(p):
        return False, "ops/openai_gate.json 없음 — 사용자 확인 전 OpenAI 호출 차단"
    g = json.load(open(p, encoding="utf-8"))
    need = ["credit_org_project", "credit_balance_checked", "auto_recharge_off", "payment_method_status",
            "project_hard_limit_usd", "confirmed_by_user_at"]
    miss = [k for k in need if not g.get(k)]
    if miss or not g.get("enabled"):
        return False, f"사용자 확인 미완료: {miss or ['enabled=false']}"
    return True, "ok"


def _limits(g: dict):
    import dataclasses
    from tools.agents_pilot import LIMITS
    ch = {k: g[k] for k in ("max_calls_per_day", "max_concurrency", "max_retries") if g.get(k) is not None}
    if g.get("budget_usd") is not None:
        ch["budget_usd"] = float(g["budget_usd"])
    if g.get("max_calls_total"):
        ch["max_calls_total"] = int(g["max_calls_total"])
    return dataclasses.replace(LIMITS, **ch)


def cmd_run_openai(L: RepoLedger, args, http=None, fetch=None) -> dict:
    """새 승인 지시나 재개할 작업이 있을 때만 OpenAI를 호출한다(없으면 호출 0회로 끝)."""
    from collectors.law_go_kr import urllib_fetch
    from collectors.retry import RetryingFetch
    from kernel.agents_pipeline import run_chain
    from kernel.status_publish import publish
    published: list[dict] = []
    from kernel.budget import Budget
    from kernel.openai_agents import recover_pending, urllib_transport
    from tools.agents_pilot import PRICES, DRAFT_MODEL, REVIEW_MODEL
    now = _iso(args)
    now_s = _now(args)
    jobs = _jobs(L.root)
    store = dv.DirectiveStore(L.root, L.write_json)
    g = _gate_cfg(L.root)
    ok, why = _openai_gate(L.root) if http is None else (True, "mock transport")
    if ok and g.get("window_end_epoch") and now_s >= g["window_end_epoch"]:
        ok, why = False, "시험 기간 종료 — 새 호출 없음(미정산 호출 복구만)"
    if ok and g.get("window_start_epoch") and now_s < g["window_start_epoch"]:
        ok, why = False, "시험 기간 시작 전"
    ran = []
    budget = Budget(os.path.join(L.root, g["budget_file"]), "openai", _limits(g), PRICES,
                    clock=(lambda: _now(args)) if args.now else time.time)
    recovered = []
    if budget.s.get("pending") and (ok or "종료" in why):       # 결과 불확실 호출: 상태부터 확인(재호출 아님)
        recovered = recover_pending(budget, http or urllib_transport)

    def hold(j, jid, reason):
        if j.get("state_reason") != reason:
            j["state_reason"], j["updated_at"] = reason, now
            L.write_json(f"ledger/jobs/{jid}.json", j, f"job: {jid} held")
    if not ok:
        for jid, j in jobs.items():
            if j.get("state") in ("accepted", "running"):
                hold(j, jid, why)
        dv.refresh(store, L, _jobs(L.root), now)
        return {"ran": [], "gate": why, "recovered": recovered, "openai_calls": 0}
    http = http or urllib_transport
    run_owner = getattr(args, "run", None)

    def guard():
        if run_owner and not L.renew_lock(run_owner, _now(args) if args.now else time.time()):
            raise LockLost(f"잠금 연장 실패({run_owner}) — 호출 중단")
    started = sum(1 for j in jobs.values() if j.get("steps", {}).get("draft"))
    calls_before = len(budget.s["calls"])
    for jid, j in jobs.items():
        if j.get("state") not in ("accepted", "running"):
            continue
        if len(ran) >= int(args.max_jobs or 1):
            break
        d = store.get(j.get("directive_id", "")) or {}
        if g.get("require_test_directive") and not d.get("test"):
            hold(j, jid, "24시간 시험: 시험 표시 지시만 실행")
            continue
        fresh = not j.get("steps", {}).get("draft")
        if fresh and g.get("max_jobs_total") and started >= int(g["max_jobs_total"]):
            hold(j, jid, f"시험 작업 수 상한({g['max_jobs_total']}) — 실행 안 함")
            continue
        out_dir = os.path.join("jobs", jid)

        def save(job, jid=jid):
            L.write_json(f"ledger/jobs/{jid}.json", job, f"job: {jid} {job.get('state', '')}")
            if getattr(args, "publish_dir", None):   # 단계가 바뀔 때마다 공개 상태 갱신(실패해도 연구는 계속)
                published.append(publish(L.root, args.publish_dir, _now(args),
                                         push=bool(getattr(args, "push", False))))

        def write_text(name, text, out_dir=out_dir):
            rel = out_dir + "/" + "/".join(_safe(x) for x in name.split("/"))
            p = os.path.join(L.root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            L._commit_push([rel], f"job {jid}: {name}")
            return rel
        j["state"] = "running"
        started += int(fresh)
        try:
            run_chain(j, budget, http, fetch or RetryingFetch(urllib_fetch), save, write_text,
                      {"draft": DRAFT_MODEL, "review": REVIEW_MODEL}, clock=lambda: _now(args) if args.now else time.time(),
                      turn_kwargs={"sleep": (lambda s: None) if args.now else time.sleep}, guard=guard,
                      max_articles=int(g.get("max_articles") or 0))
        except LockLost as e:
            j["state_reason"] = str(e)
            ran.append({"job": jid, "state": j.get("state"), "reason": str(e)})
            break                                   # 잠금을 잃으면 이번 실행의 나머지 작업도 중단
        except Exception as exc:  # noqa: BLE001 — 한 작업의 실패가 다른 작업을 막지 않게
            j["state"], j["state_reason"] = "failed", f"program_error: {str(exc)[:200]}"
            save(j)
        ran.append({"job": jid, "state": j["state"], "reason": j.get("state_reason", "")})
    dv.refresh(store, L, _jobs(L.root), now)
    return {"ran": ran, "recovered": recovered, "budget": budget.report(),
            "openai_calls": len(budget.s["calls"]) - calls_before,
            "published": [p for p in published if p.get("changed") or p.get("error")][-8:]}


def cmd_schedule_record(L: RepoLedger, args) -> dict:
    """관리자 세션이 get_trigger 결과(JSON 파일)를 넘기면 예약의 실제 상태를 기록한다.
    예약 존재·활성 여부는 '연구가 됐는가'와 별개로 기록한다."""
    t = json.load(open(args.trigger_json, encoding="utf-8"))
    t = t.get("trigger", t)
    cfg = _cfg(L.root) or {}
    obs = {"observed_at": _iso(args), "enabled": t.get("enabled"), "next_run_at": t.get("next_run_at"),
           "cron_expression": t.get("cron_expression"), "ended_reason": t.get("ended_reason"),
           "last_run": t.get("last_run"), "trigger_id": t.get("id")}
    cfg.setdefault("trigger_observations", []).append(obs)
    cfg["trigger_observations"] = cfg["trigger_observations"][-48:]
    expired = bool(cfg.get("end_at_epoch")) and _now(args) >= cfg["end_at_epoch"]
    if expired:
        cfg["end_verified"] = obs["enabled"] is False
        if obs["enabled"] is not False:
            cfg["end_problem"] = f"{obs['observed_at']} 종료 시각 이후에도 enabled={obs['enabled']} — 자동 재개 없이 보고 필요"
    L.write_json("ops/schedule.json", cfg, f"schedule: observed enabled={obs['enabled']}")
    L.record(f"trigger-obs:{obs['observed_at']}", type="trigger_observed", **{k: obs[k] for k in ("enabled", "next_run_at")})
    return {"recorded": obs, "end_verified": cfg.get("end_verified")}


def cmd_public_state(L: RepoLedger, args) -> dict:
    """공개 상태(PublicLabState) 파일을 만든다. 게시는 하지 않는다(위치·공개 범위는 사용자 승인 후)."""
    from kernel.public_state import build
    body = build(L.root, _now(args))
    out = args.out or os.path.join(L.root, "status", "public_state.preview.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    return {"written": out, "revision": body["revision"], "events": len(body["events"]),
            "active": [a["id"] for a in body["agents"] if a["state"] != "idle"]}


def cmd_publish_state(L: RepoLedger, args) -> dict:
    """공개 상태를 --dest(공개 저장소 클론)의 status.json에 쓴다. --push가 있을 때만 커밋·푸시(사용자 승인 후 예약에서 사용)."""
    from kernel.status_publish import publish
    return publish(L.root, args.dest, _now(args), push=bool(args.push))


def cmd_lock(L: RepoLedger, args) -> dict:
    """매 실행 첫 단계. 잠금을 못 잡으면 이번 실행은 아무것도 하지 않고 끝낸다(이중 실행 방지)."""
    ok, cur = L.try_lock(args.run, _now(args))
    L.record(f"lock:{args.run}", type="manager_lock", run=args.run, acquired=ok,
             held_by=None if ok else cur.get("owner"))
    return {"acquired": ok, "held_by": None if ok else cur.get("owner"), "expires_at": cur.get("expires_at")}


def cmd_unlock(L: RepoLedger, args) -> dict:
    L.release_lock(args.run, _now(args))
    return {"released": args.run}


def _iso(args) -> str:
    return dt.datetime.fromtimestamp(_now(args), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    ap.add_argument("cmd", choices=["plan-observe", "tick", "adopt", "summary", "prompt", "inbox", "run-openai", "lock", "unlock", "schedule-record", "public-state", "publish-state"])
    ap.add_argument("--out")
    ap.add_argument("--dest")
    ap.add_argument("--publish-dir")
    ap.add_argument("--trigger-json")
    ap.add_argument("--rows")
    ap.add_argument("--max-jobs")
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
               "summary": cmd_summary, "inbox": cmd_inbox, "run-openai": cmd_run_openai,
               "lock": cmd_lock, "unlock": cmd_unlock,
               "schedule-record": cmd_schedule_record, "public-state": cmd_public_state, "publish-state": cmd_publish_state}[a.cmd](L, a)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
