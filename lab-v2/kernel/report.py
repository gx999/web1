"""연구 결과 보고서(비공개 원장 전용). 지피가 읽어 사용자에게 파일로 보고한다.

- 위치: reports/<지시ID>.md (비공개 저장소 gx999/daeyong-lab). 공개 상태 파일·홈페이지로는 절대 나가지 않는다.
- 목록: status/reports.json — 지피는 이 목록에서 `reported_by_gippy`가 없는 항목을 찾아 보고한다.
- 내용은 원장 기록을 옮겨 적기만 한다(새 판단·요약 생성 없음). 시각은 원장 기록값.
"""
from __future__ import annotations

import datetime as dt
import json
import os

STATUS_TEXT = {"retrieved": "확보", "transient_failed": "일시 실패(보류)", "access_restricted": "접근 제한(보류)",
               "search_only": "검색 결과만", "shell_only": "본문 없음"}


def _kst(x) -> str:
    if x is None:
        return "-"
    if isinstance(x, (int, float)):
        t = dt.datetime.fromtimestamp(x, dt.timezone.utc)
    else:
        try:
            t = dt.datetime.strptime(str(x), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return str(x)
    return (t + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")


def build_markdown(job: dict) -> str:
    did, steps = job.get("directive_id", ""), job.get("steps", {})
    final_name = "revise" if steps.get("revise", {}).get("status") == "done" else "draft"
    final = steps.get(final_name, {})
    review = steps.get("review", {})
    srcs = steps.get("collect", {}).get("sources", [])
    cost = sum(float(s.get("cost") or 0) for s in steps.values() if isinstance(s, dict))
    verdict = {"approve": "승인", "revise": "수정 필요 → 수정본 반영"}.get(job.get("verdict"), job.get("verdict") or "-")
    L = [f"# 연구 결과 보고 · 지시 {did}" + (" (시험)" if job.get("test") else ""), "",
         "> AI 연구원(초안·독립 검토·수정)이 만든 **검토 전 초안**입니다. 사용자 확인 전에는 공개·투고·외부 전송하지 않습니다.", "",
         "| 항목 | 내용 |", "|---|---|",
         f"| 상태 | {job.get('state', '-')} · {job.get('state_reason', '')} |",
         f"| 접수 | {_kst(job.get('created_at'))} |", f"| 완료 | {_kst(job.get('updated_at'))} |",
         f"| 독립 검토 판정 | {verdict} |",
         f"| 근거 원문 | {sum(1 for s in srcs if s.get('status') == 'retrieved')}건 확보 / {len(srcs)}건 요청 |",
         f"| 내부 기록 비용 | ${cost:.4f} (OpenAI 사용량 기준 추정, 청구액 아님) |", "",
         "## 지시 내용", "", "```", (job.get("question") or "").strip(), "```", "",
         f"## 결과물 ({'검토 반영 수정본' if final_name == 'revise' else '초안'})", "", (final.get("text") or "(본문 없음)").strip(), "",
         "## 독립 검토 의견", "", (review.get("text") or "(검토 없음)").strip(), "",
         "## 근거 자료", "", "| 법령 | 조문 | 시행일 | 상태 | 원문 | SHA-256 |", "|---|---|---|---|---|---|"]
    for s in srcs:
        L.append(f"| {s.get('law', '')} | {s.get('article', '')} | {s.get('effective_date', '')} | {STATUS_TEXT.get(s.get('status'), s.get('status'))} | "
                 f"{('[국가법령정보센터](' + s['url'] + ')') if s.get('url') else '-'} | {(s.get('sha256') or '')[:12]} |")
    L += ["", "## 처리 기록", "", "| 단계 | 모델 | 시작 | 완료 | 상태 |", "|---|---|---|---|---|"]
    for name, label in (("collect", "원문 수집"), ("draft", "초안"), ("review", "독립 검토"), ("revise", "수정")):
        s = steps.get(name)
        if s:
            L.append(f"| {label} | {s.get('model', '-')} | {_kst(s.get('started_at'))} | {_kst(s.get('completed_at') or s.get('at'))} | {s.get('status', '-')} |")
    missing = [s for s in srcs if s.get("status") != "retrieved"]
    L += ["", "## 확인 필요", ""]
    if missing:
        L.append("- 확보하지 못한 원문: " + ", ".join(f"{s.get('law')} {s.get('article')}" for s in missing) + " — 해당 부분 주장은 결과물에서 제외되거나 '확인 필요'로 표시됨.")
    if final_name == "revise":
        L.append("- 수정본은 재검토를 거치지 않았습니다(검토 1회 + 수정 1회 구조).")
    L.append("- 현행 법령 기준입니다. 사건 당시 적용 법령은 다를 수 있습니다.")
    L.append(f"- 원장 파일: ledger/jobs/{job.get('id')}.json, jobs/{job.get('id')}/")
    return "\n".join(L) + "\n"


def write_report(L, job: dict, now: float) -> str | None:
    """완료된 작업이면 보고서와 목록을 쓴다(같은 내용이면 다시 쓰지 않음). L은 RepoLedger."""
    if job.get("state") != "done":
        return None
    did = job.get("directive_id") or job["id"]
    rel = f"reports/{did}.md"
    text = build_markdown(job)
    p = os.path.join(L.root, rel)
    if not (os.path.exists(p) and open(p, encoding="utf-8").read() == text):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        L._commit_push([rel], f"report: {did}")
    idx_p = os.path.join(L.root, "status", "reports.json")
    idx = json.load(open(idx_p, encoding="utf-8")) if os.path.exists(idx_p) else {"reports": []}
    if not any(r["directive_id"] == did for r in idx["reports"]):
        idx["reports"].append({"directive_id": did, "job": job["id"], "path": rel, "test": bool(job.get("test")),
                               "verdict": job.get("verdict"), "ready_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "reported_by_gippy": None})
        L.write_json("status/reports.json", idx, f"reports: {did} ready")
    return rel
