"""비용 차단 장치 모의시험 — 가짜 요금·가짜 전송. 실제 API 호출 없음.
여기 요금 값은 시험용 임의값이며 실제 요금이 아니다."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.budget import Budget, BudgetBlocked, Limits, Price  # noqa: E402
from kernel.openai_worker import PLACEHOLDER_KEY, call_model  # noqa: E402

FAKE_PRICES = {"test-model": Price(input_per_1m=1.0, output_per_1m=4.0, source="시험용 가짜 값")}


def mk(tmp_path, budget=0.05, calls=10, out=1000, inp=5000):
    return Budget(str(tmp_path / "ops" / "budget_openai.json"), "openai",
                  Limits(budget_usd=budget, max_calls_per_day=calls, max_output_tokens=out, max_input_tokens=inp),
                  FAKE_PRICES, clock=lambda: 1_000_000.0)


class FakeHTTP:
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def __call__(self, url, headers, body, timeout):
        self.sent.append((url, headers, body))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


OK = (200, {"output_text": "검토 의견", "usage": {"input_tokens": 1000, "output_tokens": 500}})


def test_unknown_price_blocks_before_sending(tmp_path):
    b, http = mk(tmp_path), FakeHTTP(OK)
    r = call_model(b, http, "c1", "gpt-unconfirmed", "x", 100)
    assert r["blocked"] and "요금표 미확인" in r["blocked"] and http.sent == []


def test_successful_call_settles_actual_usage_and_no_real_key(tmp_path):
    b, http = mk(tmp_path), FakeHTTP(OK)
    r = call_model(b, http, "c1", "test-model", "x", 1000)
    assert r["ok"] and abs(r["cost"] - (1000 / 1e6 * 1 + 500 / 1e6 * 4)) < 1e-12
    assert http.sent[0][1]["Authorization"] == f"Bearer {PLACEHOLDER_KEY}"   # 실제 키 없음
    assert http.sent[0][2]["max_output_tokens"] == 1000 and http.sent[0][2]["store"] is False


def test_worst_case_reservation_blocks_when_budget_small(tmp_path):
    b, http = mk(tmp_path, budget=0.004), FakeHTTP(OK)   # 최악 1000*1e-6 + 1000*4e-6 = 0.005
    r = call_model(b, http, "c1", "test-model", "x", 1000)
    assert "잔여 예산" in r["blocked"] and http.sent == []


def test_credit_exhausted_error_kills_provider_no_retry(tmp_path):
    b = mk(tmp_path)
    http = FakeHTTP((429, {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}), OK)
    r = call_model(b, http, "c1", "test-model", "x", 100)
    assert not r["ok"] and len(http.sent) == 1                  # 재시도 안 함
    assert "쿼터" in b.report()["killed"] or "quota" in b.report()["killed"]
    r2 = call_model(b, FakeHTTP(OK), "c2", "test-model", "x", 100)
    assert "정지 상태" in r2["blocked"]


def test_balance_exhausted_message_kills(tmp_path):
    b = mk(tmp_path)
    call_model(b, FakeHTTP((400, {"error": {"code": "credit_balance_exhausted", "message": "credit balance exhausted"}})),
               "c1", "test-model", "x", 100)
    assert b.report()["killed"]


def test_transient_retries_once_then_conservative_charge(tmp_path):
    b = mk(tmp_path)
    http = FakeHTTP((503, {"error": {"message": "overloaded"}}), TimeoutError("timed out"))
    r = call_model(b, http, "c1", "test-model", "x", 1000)
    assert not r["ok"] and len(http.sent) == 2                  # 1회 재시도
    assert b.report()["spent_usd"] == pytest.approx(0.005)      # 사용량 미확인 → 최악값 정산


def test_daily_cap_concurrency_duplicate_and_budget_exhaust(tmp_path):
    b = mk(tmp_path, budget=0.0105, calls=3)
    for i in range(2):
        assert call_model(b, FakeHTTP(OK), f"c{i}", "test-model", "x", 1000)["ok"]
    assert call_model(b, FakeHTTP(OK), "c0", "test-model", "x", 10)["blocked"].startswith("중복")
    r = call_model(b, FakeHTTP(OK), "c3", "test-model", "x", 1000)   # 잔여 0.0045 < 최악 0.005
    assert "잔여 예산" in r["blocked"]
    b2 = mk(tmp_path / "x", calls=1)
    b2.reserve("a", "test-model", 10)
    with pytest.raises(BudgetBlocked):
        b2.reserve("b", "test-model", 10)                        # 일일·동시 상한


def test_state_persists_across_restart(tmp_path):
    b = mk(tmp_path)
    call_model(b, FakeHTTP(OK), "c1", "test-model", "x", 1000)
    b.kill("수동 정지")
    b2 = mk(tmp_path)                                            # 관리자 재시작 모사
    assert b2.report()["spent_usd"] > 0 and b2.report()["killed"] == "수동 정지"


def test_extracts_text_from_raw_responses_shape(tmp_path):
    from kernel.openai_worker import extract_text
    raw = {"id": "resp_x", "status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "요약 1"}, {"type": "output_text", "text": "요약 2"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5}}
    assert extract_text(raw) == "요약 1\n요약 2"
    b = mk(tmp_path)
    r = call_model(b, FakeHTTP((200, raw)), "c1", "test-model", "x", 10)
    assert r["text"] == "요약 1\n요약 2" and r["response_id"] == "resp_x"


@pytest.mark.parametrize("code", ["project_spend_limit_exceeded", "organization_spend_limit_exceeded",
                                  "organization_usage_limit_exceeded", "credit_balance_exhausted"])
def test_official_429_billing_codes_stop_without_retry(tmp_path, code):
    b = mk(tmp_path)
    http = FakeHTTP((429, {"error": {"code": code, "message": "limit"}}), OK)
    r = call_model(b, http, "c1", "test-model", "x", 100)
    assert not r["ok"] and len(http.sent) == 1 and b.report()["killed"]
