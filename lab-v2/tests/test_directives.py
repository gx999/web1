"""지시함 ↔ 실행 결과 연결 모의시험 — 로컬 git 원장, 가짜 Notion 행, 가짜 OpenAI, 가짜 법령 사이트.
실제 지시·실제 연구 성과가 아니다. 실제 API 호출 없음."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collectors.retry import RetryingFetch  # noqa: E402
from kernel.ledger import ConflictError  # noqa: E402
from kernel.repo_ledger import RepoLedger  # noqa: E402
from tools import manager_run as mr  # noqa: E402

SHELL = ('<html><iframe src="/LSW//lsInfoP.do?lsiSeq=1&amp;efYd=20260911&amp;x=0"></iframe></html>').encode()
BODY = ("<div>제34조(개인정보 유출등의 통지ㆍ신고) ① 개인정보처리자는 지체 없이 ... </div>"
        "<div>제34조의2(노출된 개인정보의 삭제) ① 내용</div>").encode()


def law_site(fail_law=None):
    calls = []

    def f(url):
        calls.append(url)
        if fail_law and fail_law in url:
            return 0, b""                     # 연결 실패 지속
        return (200, SHELL) if "%EB%B2%95%EB%A0%B9" in url else (200, BODY)
    f.calls = calls
    return f


def _origin(tmp_path):
    bare = tmp_path / "o.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return bare


def _clone(tmp_path, bare, name, push=False):
    wd = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(bare), str(wd)], check=True, capture_output=True)
    if not (wd / ".git" / "refs" / "heads" / "main").exists() and \
            subprocess.run(["git", "-C", str(wd), "rev-parse", "HEAD"], capture_output=True).returncode:
        subprocess.run(["git", "-C", str(wd), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)
        subprocess.run(["git", "-C", str(wd), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
    return RepoLedger(str(wd), push=push)


@pytest.fixture
def L(tmp_path):
    return _clone(tmp_path, _origin(tmp_path), "wd")


def row(rid, did, kind, content, target="", test=True):
    return {"url": f"https://app.notion.com/p/{rid}", "요청ID": did, "종류": kind, "내용": content, "대상": target,
            "우선순위": None, "시험": "__YES__" if test else "__NO__", "작성자": "지피",
            "작성 시각": "2026-11-10T07:09:11.415Z"}


def inbox(L, tmp_path, rows, now=1_800_000_000):
    p = tmp_path / "rows.json"
    p.write_text(json.dumps({"results": rows}, ensure_ascii=False))
    return mr.cmd_inbox(L, NS(rows=str(p), now=str(now)))


def idx(L):
    return json.load(open(os.path.join(L.root, "status/directives/index.json"), encoding="utf-8"))


def d(L, did):
    return json.load(open(os.path.join(L.root, f"status/directives/{did}.json"), encoding="utf-8"))


class FakeOpenAI:
    """세션 생성·메시지마다 즉시 완료되는 턴. 검토 지시면 revise 판정을 돌려준다."""
    def __init__(self, crash_on=None):
        self.sessions, self.prompts, self.crash_on, self.n = {}, [], crash_on, 0

    def __call__(self, method, url, headers, body, timeout):
        path = url.split("/v1")[1]
        if method == "POST" and path == "/agents/sessions":
            self.n += 1
            sid = f"sess_{self.n}"
            role = "review" if "독립 검토" in body["agent"]["instructions"] else "draft"
            self.sessions[sid] = {"role": role, "turns": []}
            self._turn(sid, body["input"][0]["content"][0]["text"])
            return 201, {"id": sid}
        sid = path.split("/")[3]
        if path.endswith("/events"):
            ev = body["events"][0]
            if ev["type"] == "agent.session.input.message":
                self._turn(sid, ev["input"][0]["content"][0]["text"], revise=True)
            return 200, {}
        if "/turns" in path:
            if self.crash_on and self.crash_on == self.sessions[sid]["role"]:
                raise KeyboardInterrupt("process killed")       # 진행 중 프로세스 종료 모사
            return 200, {"data": [t for t, _ in reversed(self.sessions[sid]["turns"])]}
        if "/items" in path:
            return 200, {"data": [{"type": "message", "role": "assistant", "turn_id": t["id"], "phase": "final_answer",
                                   "content": [{"type": "output_text", "text": txt}]}
                                  for t, txt in self.sessions[sid]["turns"]]}
        raise AssertionError(path)

    def _turn(self, sid, prompt, revise=False):
        self.prompts.append(prompt)
        s = self.sessions[sid]
        txt = ("수정본: 보류(자료 미확보) 반영" if revise else
               "판정: revise\n1. 근거 보강" if s["role"] == "review" else "초안: 제34조 비교, 제48조의3 보류(자료 미확보)")
        t = {"id": f"{sid}_t{len(s['turns'])}", "status": "completed",
             "usage": {"input_tokens": 1000, "output_tokens": 100}}
        s["turns"].append((t, txt))


def gate(L):
    L.write_json("ops/openai_gate.json", {"enabled": True, "credit_org_project": "x", "credit_balance_checked": "x",
                                          "auto_recharge_off": "x", "payment_method_status": "x",
                                          "project_hard_limit_usd": 5, "confirmed_by_user_at": "x"}, "gate (test)")


CHAIN = ("침해사고 신고·통지 비교 시험\n조문: 개인정보 보호법 제34조; 정보통신망 이용촉진 및 정보보호 등에 관한 법률 제48조의3")


def test_real_shape_question_row_is_answered_and_indexed(L, tmp_path):
    r = inbox(L, tmp_path, [row("414f568334ca81c5af9dc500a47cff85", "I-0-시험", "question", "현재 연구소 상태를 요약해 줘")])
    assert r["processed"][0]["result"] == "answered"
    i = idx(L)
    assert i["writer"] == "claude_manager" and i["rows"][0]["state"] == "answered" and i["rows"][0]["test"] is True


def test_same_row_twice_is_not_processed_twice(L, tmp_path):
    rows = [row("r1", "G-1", "question", "상태?")]
    inbox(L, tmp_path, rows)
    h = len(d(L, "G-1")["history"])
    assert inbox(L, tmp_path, rows)["processed"][0]["result"] == "duplicate"
    assert len(d(L, "G-1")["history"]) == h


def test_edit_after_intake_is_recorded_not_reexecuted(L, tmp_path):
    inbox(L, tmp_path, [row("r1", "G-1", "new_goal", "침해사고 신고 제도 비교")])
    tasks_before = len(L.all())
    r = inbox(L, tmp_path, [row("r1", "G-1", "new_goal", "침해사고 신고 제도 비교 — 범위 확대")])
    assert r["processed"][0]["result"] == "edited_after_intake" and len(L.all()) == tasks_before
    assert d(L, "G-1")["history"][-1]["state"] == "edited_after_intake"


def test_reused_request_id_from_other_row_is_rejected(L, tmp_path):
    inbox(L, tmp_path, [row("r1", "G-1", "question", "a")])
    r = inbox(L, tmp_path, [row("r2", "G-1", "question", "b")])
    assert r["processed"][0]["result"] == "rejected_duplicate_id"
    assert d(L, "G-1")["notion_row"] == "r1"


def test_owner_approval_and_scope_rules(L, tmp_path):
    r = inbox(L, tmp_path, [row("r1", "G-1", "new_goal", "침해사고 연구 결과를 홈페이지에 공개"),
                            row("r2", "G-2", "new_goal", "주식 투자 전략 연구")])
    assert [x["result"] for x in r["processed"]] == ["needs_owner", "needs_owner"]


def test_openai_chain_gated_until_user_confirms(L, tmp_path):
    inbox(L, tmp_path, [row("r1", "G-3", "new_task", CHAIN, target="openai_chain")])
    out = mr.cmd_run_openai(L, NS(now="1800000100", max_jobs="1"))
    assert out["ran"] == [] and "사용자 확인" in out["gate"]
    assert d(L, "G-3")["state"] == "accepted"                     # 실행됐다고 표시하지 않음


def test_openai_chain_end_to_end_with_missing_source_deferred(L, tmp_path):
    gate(L)
    inbox(L, tmp_path, [row("r1", "G-3", "new_task", CHAIN, target="openai_chain")])
    site, ai = law_site(fail_law="%EC%A0%95%EB%B3%B4%ED%86%B5%EC%8B%A0"), FakeOpenAI()   # 정보통신망법만 연결 실패
    out = mr.cmd_run_openai(L, NS(now="1800000100", max_jobs="1"), http=ai,
                            fetch=RetryingFetch(site, sleep=lambda s: None))
    assert out["ran"][0]["state"] == "done"
    assert "[미확보 자료]" in ai.prompts[0] and "connect_failed" in ai.prompts[0]
    x = d(L, "G-3")
    assert x["state"] == "done" and [h["state"] for h in x["history"]] == ["received", "accepted", "done"]
    ev = {e["article"]: e["status"] for e in x["evidence"]}
    assert ev == {"제34조": "retrieved", "제48조의3": "transient_failed"}
    kinds = {r["kind"]: r for r in x["results"]}
    assert set(kinds) == {"draft", "review", "revise"} and kinds["revise"]["session_id"] == kinds["draft"]["session_id"]
    assert all(os.path.exists(os.path.join(L.root, r["path"])) for r in x["results"])
    assert sum(1 for u in site.calls if "%EC%A0%95%EB%B3%B4%ED%86%B5%EC%8B%A0" in u) == 3      # 제한된 재시도 3회
    assert idx(L)["counts"] == {"done": 1}


def test_all_sources_missing_blocks_without_calling_model(L, tmp_path):
    gate(L)
    inbox(L, tmp_path, [row("r1", "G-4", "new_task", "조문: 개인정보 보호법 제34조", target="openai_chain")])
    ai = FakeOpenAI()
    out = mr.cmd_run_openai(L, NS(now="1800000100", max_jobs="1"), http=ai,
                            fetch=RetryingFetch(law_site(fail_law="%EA%B0%9C%EC%9D%B8"), sleep=lambda s: None))
    assert out["ran"][0]["state"] == "blocked" and ai.prompts == [] and d(L, "G-4")["state"] == "blocked"


def test_one_job_failure_does_not_stop_others(L, tmp_path):
    gate(L)
    inbox(L, tmp_path, [row("r1", "G-5", "new_task", "조문: 개인정보 보호법 제34조", target="openai_chain"),
                        row("r2", "G-6", "new_task", "조문: 개인정보 보호법 제34조", target="openai_chain")])

    jobs = mr._jobs(L.root)
    j5 = jobs["J-G-5"]
    j5["articles"] = "not-a-list"                                 # 손상된 작업 기록 → 프로그램 오류
    L.write_json("ledger/jobs/J-G-5.json", j5, "corrupt")
    out = mr.cmd_run_openai(L, NS(now="1800000100", max_jobs="2"), http=FakeOpenAI(),
                            fetch=RetryingFetch(law_site(), sleep=lambda s: None))
    st = {r["job"]: r["state"] for r in out["ran"]}
    assert st["J-G-5"] == "failed" and st["J-G-6"] == "done"


def test_restart_mid_review_resumes_without_repeating_finished_steps(L, tmp_path):
    gate(L)
    inbox(L, tmp_path, [row("r1", "G-7", "new_task", "조문: 개인정보 보호법 제34조", target="openai_chain")])
    ai = FakeOpenAI(crash_on="review")
    with pytest.raises(KeyboardInterrupt):
        mr.cmd_run_openai(L, NS(now="1800000100", max_jobs="1"), http=ai,
                          fetch=RetryingFetch(law_site(), sleep=lambda s: None))
    j = mr._jobs(L.root)["J-G-7"]
    assert j["steps"]["draft"]["status"] == "done" and j["steps"]["review"]["status"] == "started"
    b = json.load(open(os.path.join(L.root, "ops/budget_openai_agents.json")))
    assert list(b["reserved"]) == ["J-G-7:review:a1"]             # 예약 유지 → 잔여 예산에서 계속 차감
    ai.crash_on = None                                            # 재시작
    out = mr.cmd_run_openai(L, NS(now="1800000200", max_jobs="1"), http=ai,
                            fetch=RetryingFetch(law_site(), sleep=lambda s: None))
    assert out["recovered"][0]["action"] == "settled"             # 중단된 검토 턴을 실제 usage로 정산
    j = mr._jobs(L.root)["J-G-7"]
    assert j["state"] == "done" and j["steps"]["review"]["call_id"] == "J-G-7:review:a2"
    drafts = [p for p in ai.prompts if "조문 비교" in p or p.startswith("조문:")]
    assert len(drafts) == 1                                       # 초안은 다시 실행하지 않음
    b = json.load(open(os.path.join(L.root, "ops/budget_openai_agents.json")))
    assert not b["reserved"] and not b["pending"] and len(b["calls"]) == 4   # draft, review a1(복구), review a2, revise


def test_second_manager_cannot_take_lock_while_first_runs(tmp_path):
    bare = _origin(tmp_path)
    A = _clone(tmp_path, bare, "a", push=True)
    B = _clone(tmp_path, bare, "b", push=True)
    okA, _ = A.try_lock("run-A", 1000.0)
    okB, held = B.try_lock("run-B", 1010.0)
    assert okA and not okB and held["owner"] == "run-A"
    A.release_lock("run-A", 1100.0)
    okB, _ = B.try_lock("run-B", 1200.0)
    assert okB
    okA, _ = A.try_lock("run-A2", 1300.0)                         # B가 잡은 동안 A 재진입 불가
    assert not okA
    okA, _ = A.try_lock("run-A3", 1200.0 + 50 * 60 + 1)          # 만료(중단된 실행) 뒤에는 인수 가능
    assert okA


def test_two_managers_same_rows_conflict_then_no_double_processing(tmp_path):
    bare = _origin(tmp_path)
    A = _clone(tmp_path, bare, "a", push=True)
    B = _clone(tmp_path, bare, "b", push=True)
    rows = [row("r1", "G-8", "question", "상태?")]
    inbox(A, tmp_path, rows, now=1_800_000_000)
    with pytest.raises(ConflictError):                            # 잠금 없이 B가 늦게 같은 파일을 씀 → 거절
        inbox(B, tmp_path, rows, now=1_800_000_005)
    B.sync()
    assert inbox(B, tmp_path, rows)["processed"][0]["result"] == "duplicate"


def test_legacy_inbox_seen_row_is_migrated_not_reprocessed(L, tmp_path):
    L.write_json("inbox_seen/r9.json", {"row_id": "r9", "request_id": "I-0-시험", "result": "answered",
                                        "processed_at": "2026-11-10T07:11:35Z", "answer_ref": "status/summary.json"},
                 "legacy")
    r = inbox(L, tmp_path, [row("r9", "I-0-시험", "question", "현재 연구소 상태를 요약해 줘")])
    assert r["migrated"] == ["r9"] and r["processed"][0]["result"] == "duplicate"
    x = d(L, "I-0-시험")
    assert x["state"] == "answered" and "이관" in x["history"][0]["reason"] and len(x["history"]) == 1


def test_real_notion_markdown_row_is_normalized(L, tmp_path):
    """실측 행 모양(2026-11-10 T24-1): 대상 'openai\\_chain', 내용 줄바꿈 '<br />'."""
    gate(L)
    r = {"url": "https://app.notion.com/p/414f568334ca81a0919de0f739ed2c66", "요청ID": "T24-1", "종류": "new_task",
         "대상": "openai\\_chain", "시험": "__YES__", "작성자": "지피", "우선순위": None,
         "내용": "침해사고 신고·통지 의무 비교(시험)<br />조문: 개인정보 보호법 제34조; "
                 "정보통신망 이용촉진 및 정보보호 등에 관한 법률 제48조의3"}
    out = inbox(L, tmp_path, [r])
    assert out["processed"][0]["result"] == "accepted"
    j = mr._jobs(L.root)["J-T24-1"]
    assert j["articles"] == [["개인정보 보호법", "제34조"], ["정보통신망 이용촉진 및 정보보호 등에 관한 법률", "제48조의3"]]
    assert inbox(L, tmp_path, [r])["processed"][0]["result"] == "duplicate"     # 같은 원 행 재조회 → 중복
