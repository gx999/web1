"""비공개 원장 → 공개 상태(홈페이지 v1 `PublicLabState` 형식) 변환.

공개 화면(생활관·홈페이지)이 읽는 유일한 데이터. 허용 목록 값만 만든다:
- 문장은 아래 고정 문구(STEP_TEXT 등)와 숫자·ID·시각으로만 조립. 원고·출처 원문·프롬프트·Notion 내용·
  세션 ID·모델 이름·비용·오류 원문은 넣지 않는다(넣을 경로 자체가 없음).
- 지시 제목은 공개 표시가 있는 지시만(현재 지시함에 그런 열이 없으므로 항상 '비공개 과제' / 시험이면 '시험 과제').
- `assert_lab_state_safe`가 키·길이·금지 패턴을 다시 검사한다. 통과하지 못하면 내보내지 않는다.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
from typing import Any

from .privacy import scan

# 원장 역할 → 생활관 캐릭터(v1 lab-types.ts catalog의 id)
STAGE_AGENT = {"collect": "research", "draft": "writer", "review": "reviewer", "revise": "writer"}
AGENTS = [("lead", "지피", "연구팀장"), ("research", "미나", "자료 조사"), ("writer", "솔", "논증 집필"),
          ("reviewer", "클로", "독립 검증"), ("experiment", "코디", "실험 재현")]
STEP_STATE = {"collect": "researching", "draft": "writing", "review": "reviewing", "revise": "writing"}
STEP_TEXT = {"collect": "공식 원문 수집", "draft": "초안 작성", "review": "독립 검토", "revise": "검토 반영 수정"}
VERDICT_TEXT = {"approve": "검토 판정: 승인", "revise": "검토 판정: 수정 필요"}
JOB_STATE = {"accepted": "queued", "running": "running", "done": "done", "failed": "failed", "blocked": "blocked",
             "blocked_budget": "blocked", "waiting_user": "waiting", "waiting_limit": "waiting"}
HANDOFF = [("collect", "research", "writer", "원문 전달"), ("draft", "writer", "reviewer", "초안을 검토로"),
           ("review", "reviewer", "writer", "검토 의견 전달")]


def _iso(x: Any) -> str | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(x)


def _read(p: str, default=None):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def build(root: str, now: float, revision: int | None = None, include_tests: bool = True, max_events: int = 60) -> dict:
    jobs = [json.load(open(p, encoding="utf-8")) for p in sorted(glob.glob(os.path.join(root, "ledger", "jobs", "*.json")))]
    if not include_tests:
        jobs = [j for j in jobs if not j.get("test")]
    events: list[dict] = []
    projects: list[dict] = []
    active: dict[str, dict] = {}       # 캐릭터별 현재 진행 단계(진행 중인 작업만)
    for j in jobs:
        jid, did, test = j["id"], j.get("directive_id", ""), bool(j.get("test"))
        title = "시험 과제" if test else "비공개 과제"
        st = j.get("state", "accepted")
        projects.append({"id": did, "title": title, "status": JOB_STATE.get(st, "unknown"), "priority": 3,
                         "updatedAt": j.get("updated_at") or j.get("created_at"), "artifactVersion": ""})
        events.append({"id": f"{jid}:accept", "agentId": "lead", "type": "accept", "state": "queued",
                       "summary": f"지시 {did} 접수" + (" (시험)" if test else ""), "at": j.get("created_at")})
        steps = j.get("steps", {})
        col = steps.get("collect", {})
        if col or st == "running":
            events.append({"id": f"{jid}:collect:start", "agentId": "research", "type": "step_start", "state": "researching",
                           "summary": STEP_TEXT["collect"] + " 시작", "at": j.get("created_at")})
            if not col.get("status") == "done" and st == "running":
                active["research"] = {"state": "researching", "summary": STEP_TEXT["collect"] + " 중", "taskId": did, "observedAt": j.get("updated_at") or j.get("created_at")}
        if col.get("status") == "done":
            src = col.get("sources", [])
            ok = sum(1 for s in src if s.get("status") == "retrieved")
            events.append({"id": f"{jid}:collect", "agentId": "research", "type": "step_done", "state": "researching",
                           "summary": f"{STEP_TEXT['collect']} · {len(src)}건 중 {ok}건 확보" + ("" if ok == len(src) else " (나머지 보류)"),
                           "at": col.get("at")})
        for name in ("draft", "review", "revise"):
            s = steps.get(name)
            if not s:
                continue
            who = STAGE_AGENT[name]
            if s.get("status") == "started":
                active[who] = {"state": STEP_STATE[name], "summary": STEP_TEXT[name] + " 중", "taskId": did, "observedAt": s.get("at")}
                events.append({"id": f"{jid}:{name}:start:{s.get('attempt', 1)}", "agentId": who, "type": "step_start",
                               "state": STEP_STATE[name], "summary": STEP_TEXT[name] + " 시작", "at": s.get("at")})
                continue
            if s.get("started_at"):
                events.append({"id": f"{jid}:{name}:start:{s.get('attempt', 1)}", "agentId": who, "type": "step_start",
                               "state": STEP_STATE[name], "summary": STEP_TEXT[name] + " 시작", "at": _iso(s["started_at"])})
            done_ok = s.get("status") == "done"
            summary = STEP_TEXT[name] + (" 완료" if done_ok else " 멈춤")
            if name == "review" and done_ok and j.get("verdict") in VERDICT_TEXT:
                summary = VERDICT_TEXT[j["verdict"]]
            if name in ("draft", "revise") and done_ok and isinstance(s.get("chars"), int):
                summary += f" · {s['chars']:,}자"
            events.append({"id": f"{jid}:{name}:done:{s.get('attempt', 1)}", "agentId": who, "type": "step_done" if done_ok else "step_failed",
                           "state": STEP_STATE[name] if done_ok else "failed", "summary": summary, "at": _iso(s.get("completed_at")) or s.get("at")})
        for stage, a, b, text in HANDOFF:
            nxt = {"collect": "draft", "draft": "review", "review": "revise"}[stage]
            if steps.get(stage, {}).get("status") == "done" and steps.get(nxt):
                at = _iso(steps[nxt].get("started_at")) or steps[nxt].get("at")
                events.append({"id": f"{jid}:handoff:{stage}", "agentId": a, "type": "handoff", "toAgentId": b, "summary": text, "at": at})
        if st == "done":
            events.append({"id": f"{jid}:complete", "agentId": "lead", "type": "complete", "state": "done",
                           "summary": f"지시 {did} 완료 기록", "at": j.get("updated_at")})
        elif st in ("blocked", "blocked_budget", "failed"):
            events.append({"id": f"{jid}:{st}", "agentId": "lead", "type": "blocked", "state": "blocked",
                           "summary": f"지시 {did} 멈춤 · 사용자·지피 확인 필요", "at": j.get("updated_at")})
    # 결과 보고서: 준비됨(지피가 사용자에게 전달할 차례) / 지피 보고 완료
    rep = _read(os.path.join(root, "status", "reports.json"), {}) or {}
    for r in rep.get("reports", []):
        if not include_tests and r.get("test"):
            continue
        did = r.get("directive_id", "")
        events.append({"id": f"report:{did}", "agentId": "lead", "type": "report_ready", "state": "reporting",
                       "summary": f"지시 {did} 결과 보고서 준비", "at": r.get("ready_at")})
        if r.get("reported_by_gippy"):
            events.append({"id": f"reported:{did}", "agentId": "lead", "type": "reported", "state": "idle",
                           "summary": f"지시 {did} 사용자에게 보고 완료", "at": r.get("reported_by_gippy")})
    # 관리자 매시 점검(잠금 기록) = 지피 순찰
    lock = _read(os.path.join(root, "ops", "manager_lock.json"), {}) or {}
    runs = sorted(glob.glob(os.path.join(root, "ops", "runs", "*.json")))[-6:]
    for p in runs:
        rid = os.path.basename(p)[:-5]
        m = re.match(r"run-(\d{8})T(\d{6})Z", rid)
        if m:
            at = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
            events.append({"id": f"patrol:{rid}", "agentId": "lead", "type": "patrol", "state": "idle", "summary": "매시 점검", "at": at})
    running_lock = bool(lock) and lock.get("released_at") is None and now < lock.get("expires_at", 0)
    order = {"accept": 0, "step_done": 1, "step_failed": 1, "handoff": 2, "step_start": 3, "complete": 4, "blocked": 4, "report_ready": 5, "reported": 6, "patrol": 7}
    events = sorted([e for e in events if e.get("at")], key=lambda e: (e["at"], order.get(e["type"], 9)))[-max_events:]
    observed = max([e["at"] for e in events], default=None)
    agents = []
    for aid, name, role in AGENTS:
        a = {"id": aid, "name": name, "role": role, "state": "idle", "summary": "", "taskId": None, "observedAt": observed, "workerHeartbeatAt": None}
        if aid in active:
            a.update(active[aid]); a["workerHeartbeatAt"] = active[aid]["observedAt"]
        if aid == "lead" and running_lock:
            a.update(state="reporting", summary="관리자 점검 중")
        agents.append(a)
    body = {"mode": "live", "revision": revision if revision is not None else len(events),
            "serverTime": _iso(now), "sourceObservedAt": observed,
            "execution": {"enabled": True, "reason": ""},
            "lab": {"name": "정대용 디지털 연구소", "motto": "", "status": "running" if active or running_lock else "idle", "paused": False},
            "agents": agents, "projects": projects[-20:], "events": events, "reports": []}
    assert_lab_state_safe(body)
    return body


ALLOWED = {
    "top": {"mode", "revision", "serverTime", "sourceObservedAt", "execution", "lab", "agents", "projects", "events", "reports"},
    "agents": {"id", "name", "role", "state", "summary", "detail", "taskId", "observedAt", "workerHeartbeatAt"},
    "projects": {"id", "title", "status", "priority", "updatedAt", "artifactVersion"},
    "events": {"id", "agentId", "type", "state", "summary", "at", "toAgentId"},
}
FORBIDDEN = re.compile(r"sess_|turn_|resp_|https?://|gpt-|\$|sk-|Bearer|@|/", re.I)


def assert_lab_state_safe(body: dict) -> None:
    """공개 전 마지막 검사: 허용 키만, 문자열 길이 제한, 세션·모델·주소·비용·인증정보 패턴 금지, 개인정보 정규식."""
    if set(body) - ALLOWED["top"]:
        raise ValueError(f"허용되지 않은 최상위 키: {set(body) - ALLOWED['top']}")
    for sec in ("agents", "projects", "events"):
        for row in body[sec]:
            extra = set(row) - ALLOWED[sec]
            if extra:
                raise ValueError(f"{sec}: 허용되지 않은 키 {extra}")
            for k, v in row.items():
                if isinstance(v, str):
                    if len(v) > 80:
                        raise ValueError(f"{sec}.{k}: 너무 긴 문자열")
                    if k not in ("at", "observedAt", "updatedAt", "workerHeartbeatAt") and FORBIDDEN.search(v):
                        raise ValueError(f"{sec}.{k}: 금지 패턴 {v[:30]!r}")
    if body["reports"]:
        raise ValueError("reports는 공개 승인 전까지 비워 둠")
    hits = scan(json.dumps(body, ensure_ascii=False))
    if hits:
        raise ValueError(f"개인정보 의심 패턴: {sorted({h[0] for h in hits})}")
