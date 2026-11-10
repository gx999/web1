"""외부 API 비용 차단 장치 (OpenAI 등 종량 과금 연구원 전용).

원칙
- 요금표가 확인되지 않은 모델은 호출하지 않는다(가격을 지어내지 않음).
- 호출 '전'에 최악의 비용(입력 추정 + 출력 상한)으로 잔여 예산을 검사한다(선차감·예약).
- 호출 '후' 응답의 실제 사용 토큰으로 정산한다. 사용량이 응답에 없으면 예약액을 그대로 비용으로 본다.
- 잔액 소진·쿼터·결제 관련 오류는 재시도하지 않고 해당 제공자를 정지한다(자동 유료 전환 없음).
- 정지 스위치(kill)는 원장 파일로 두어 다른 실행도 즉시 따른다.
- 프로젝트 강제 한도(제공자 측)와 별개인 2차 장치이며, 반영 지연이 있으므로 '추가 청구 0'을 보증하지 않는다.
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
from typing import Any

# 공식 문서(Spend limits, API credits) 확인 코드 포함: 429 + 아래 코드는 재시도하지 않는다.
STOP_ERRORS = ("credit_balance_exhausted", "insufficient_quota", "billing_hard_limit_reached",
               "project_spend_limit_exceeded", "organization_spend_limit_exceeded",
               "organization_usage_limit_exceeded", "spend_limit", "usage_limit_exceeded",
               "billing", "payment", "account_deactivated", "quota")


class BudgetBlocked(Exception):
    """호출 전 차단. 사유가 담긴다. 재시도 대상이 아니다."""


@dataclasses.dataclass
class Price:
    input_per_1m: float      # USD / 1M input tokens  (공식 요금표에서 사용자와 확인한 값)
    output_per_1m: float
    per_tool_call: float = 0.0   # 웹 검색 등 호출당 과금(해당 시)
    source: str = ""             # 요금표 URL·확인일


@dataclasses.dataclass
class Limits:
    budget_usd: float            # 시험 기간 내부 예산
    max_calls_per_day: int
    max_output_tokens: int
    max_input_tokens: int
    max_tool_calls_per_request: int = 0
    max_concurrency: int = 2
    max_retries: int = 1
    timeout_s: int = 120
    max_calls_total: int = 0      # 이 원장 전체(시험 기간) 호출 상한. 0=없음. 재시도(새 시도 ID)도 1회로 셈


class Budget:
    def __init__(self, path: str, provider: str, limits: Limits, prices: dict[str, Price],
                 clock=time.time) -> None:
        self.path, self.provider, self.lim, self.prices, self.now = path, provider, limits, prices, clock
        self.s = self._load()

    # ---- 상태 파일(원장에 함께 저장) --------------------------------------
    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                s = json.load(f)
            s.setdefault("pending", {})
            return s
        return {"pending": {}, "provider": self.provider, "spent_usd": 0.0, "reserved": {}, "calls": [], "killed": None}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- 계산 -----------------------------------------------------------
    def _cost(self, model: str, inp: int, out: int, tools: int = 0) -> float:
        p = self.prices[model]
        return inp / 1e6 * p.input_per_1m + out / 1e6 * p.output_per_1m + tools * p.per_tool_call

    def remaining(self) -> float:
        return self.lim.budget_usd - self.s["spent_usd"] - sum(self.s["reserved"].values())

    def _calls_today(self) -> int:
        day = int(self.now() // 86400)
        return sum(1 for c in self.s["calls"] if int(c["at"] // 86400) == day)

    # ---- 호출 전 ----------------------------------------------------------
    def reserve(self, call_id: str, model: str, est_input_tokens: int, tool_calls: int = 0) -> float:
        if self.s.get("killed"):
            raise BudgetBlocked(f"정지 상태: {self.s['killed']}")
        if model not in self.prices:
            raise BudgetBlocked(f"요금표 미확인 모델: {model} — 공식 요금 확인 전 호출 금지")
        if call_id in self.s["reserved"] or any(c["id"] == call_id for c in self.s["calls"]):
            raise BudgetBlocked(f"중복 호출 ID: {call_id}")
        if est_input_tokens > self.lim.max_input_tokens:
            raise BudgetBlocked(f"입력 토큰 상한 초과: {est_input_tokens} > {self.lim.max_input_tokens}")
        if tool_calls > self.lim.max_tool_calls_per_request:
            raise BudgetBlocked(f"도구 호출 상한 초과: {tool_calls}")
        if len(self.s["reserved"]) >= self.lim.max_concurrency:
            raise BudgetBlocked("동시 호출 상한 도달")
        if self.lim.max_calls_total and len(self.s["calls"]) + len(self.s["reserved"]) >= self.lim.max_calls_total:
            raise BudgetBlocked(f"시험 기간 총 호출 상한 도달: {self.lim.max_calls_total}")
        if self._calls_today() + len(self.s["reserved"]) >= self.lim.max_calls_per_day:
            raise BudgetBlocked("일일 호출 상한 도달")
        worst = self._cost(model, est_input_tokens, self.lim.max_output_tokens, tool_calls)
        if worst > self.remaining():
            raise BudgetBlocked(f"최악 비용 {worst:.4f} > 잔여 예산 {self.remaining():.4f}")
        self.s["reserved"][call_id] = worst
        self.s["pending"][call_id] = {"model": model, "reserved_at": self.now()}
        self._save()
        return worst

    def note_pending(self, call_id: str, **meta: Any) -> None:
        """진행 중 호출의 복구 단서(세션 ID·이전 턴 목록 등)를 전송 전에 원장에 남긴다."""
        if call_id in self.s["pending"]:
            self.s["pending"][call_id].update(meta)
            self._save()

    # ---- 호출 후 ----------------------------------------------------------
    def settle(self, call_id: str, model: str, usage: dict[str, int] | None, tool_calls: int = 0,
               error: str | None = None) -> float:
        worst = self.s["reserved"].pop(call_id, None)
        self.s["pending"].pop(call_id, None)
        if worst is None:
            raise BudgetBlocked(f"예약 없는 정산: {call_id}")
        if usage and "input_tokens" in usage and "output_tokens" in usage:
            cost = self._cost(model, usage["input_tokens"], usage["output_tokens"], tool_calls)
            basis = "usage"
        else:
            cost, basis = worst, "reserved_worst_case"   # 사용량 미확인 → 보수적으로 최악값
        self.s["spent_usd"] = round(self.s["spent_usd"] + cost, 6)
        self.s["calls"].append({"id": call_id, "model": model, "at": self.now(), "cost": cost,
                                "basis": basis, "error": (error or "")[:200]})
        if error and any(k in error.lower() for k in STOP_ERRORS):
            self.kill(f"제공자 비용·쿼터 오류: {error[:120]}")
        if self.s["spent_usd"] >= self.lim.budget_usd:
            self.kill("내부 예산 소진")
        self._save()
        return cost

    def reconcile(self, call_id: str, model: str, usage: dict[str, int], note: str = "") -> float:
        """정산 뒤 늦게 확인된 실제 사용량으로 정정. 원래 금액·근거는 남기고 차액만 반영."""
        c = next((c for c in self.s["calls"] if c["id"] == call_id), None)
        if c is None:
            raise BudgetBlocked(f"정산 기록 없음: {call_id}")
        new = self._cost(model, usage["input_tokens"], usage["output_tokens"])
        c.setdefault("corrections", []).append({"at": self.now(), "from": c["cost"], "from_basis": c["basis"],
                                                "to": new, "note": note[:200]})
        self.s["spent_usd"] = round(self.s["spent_usd"] - c["cost"] + new, 6)
        c["cost"], c["basis"] = new, "usage_reconciled"
        self._save()
        return new

    def kill(self, reason: str) -> None:
        self.s["killed"] = reason
        self._save()

    def report(self) -> dict[str, Any]:
        return {"provider": self.provider, "budget_usd": self.lim.budget_usd, "spent_usd": self.s["spent_usd"],
                "reserved_usd": round(sum(self.s["reserved"].values()), 6), "remaining_usd": round(self.remaining(), 6),
                "calls": len(self.s["calls"]), "killed": self.s["killed"],
                "note": "내부 집계(토큰×확인된 요금). 제공자 청구액과 다를 수 있음"}
