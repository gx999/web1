"""현황판용 공개 투영과 상태 전송 대기함(outbox).

공개 화면에는 허용 목록(whitelist)의 열거형 값과 시각만 나간다. 과제 제목·질문·
원고·출처 URL·세션 ID·인증정보는 구조적으로 포함될 수 없다.
"""
from __future__ import annotations

from typing import Any, Callable

from .ledger import Ledger
from .model import Status, content_hash

PUBLIC_ROLE_ALIAS = {  # 역할 → 공개 별칭(고정값). 실행 모델·세션과 무관
    "lead": "연구총괄", "collector": "자료수집", "writer": "논증·초안",
    "reviewer": "독립검토", "analyst": "데이터분석", "owner": "사용자 검토",
}
PUBLIC_STATES = {s.value for s in Status}
STALE_AFTER = 15 * 60


def public_projection(ledger: Ledger, now: float, include_tests: bool = False) -> dict[str, Any]:
    roles: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for t in ledger.all():
        if t.test and not include_tests:
            continue
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
        r = roles.setdefault(t.role, {"role": PUBLIC_ROLE_ALIAS.get(t.role, "기타"), "state": "idle"})
        if t.status == Status.RUNNING:
            r["state"] = "running"
            r["stage"] = t.stage.value
        elif r["state"] == "idle" and t.status in (Status.BLOCKED, Status.WAITING_LIMIT, Status.WAITING_USER):
            r["state"] = t.status.value
    body = {"generated_at": now, "roles": sorted(roles.values(), key=lambda r: r["role"]), "counts": counts}
    body["hash"] = content_hash(body)
    return body


def assert_public_safe(payload: dict[str, Any]) -> None:
    allowed_top = {"generated_at", "roles", "counts", "hash", "stale"}
    if set(payload) - allowed_top:
        raise ValueError(f"비공개 필드 포함: {set(payload) - allowed_top}")
    for r in payload["roles"]:
        if set(r) - {"role", "state", "stage"}:
            raise ValueError(f"역할 항목에 비허용 필드: {set(r) - {'role', 'state', 'stage'}}")
        if r["state"] not in PUBLIC_STATES | {"idle"}:
            raise ValueError("허용되지 않은 상태 값")


class Outbox:
    """상태 전송: 실패하면 보관 후 재전송. 수신 측은 오래된 정보를 stale로 표시."""

    def __init__(self, send: Callable[[dict[str, Any]], None], max_attempts: int = 5) -> None:
        self.send = send
        self.pending: list[dict[str, Any]] = []
        self.delivered_hashes: set[str] = set()
        self.max_attempts = max_attempts
        self.log: list[tuple[str, str]] = []

    def publish(self, payload: dict[str, Any]) -> None:
        assert_public_safe(payload)
        if payload["hash"] in self.delivered_hashes:
            return  # 같은 내용 재전송 생략
        self.pending.append({"payload": payload, "attempts": 0})
        self.flush()

    def flush(self) -> None:
        keep = []
        for item in self.pending:
            # 최신 것만 의미가 있으므로 오래된 미전송 항목은 최신 항목으로 대체
            if item is not self.pending[-1]:
                self.log.append((item["payload"]["hash"], "superseded"))
                continue
            try:
                item["attempts"] += 1
                self.send(item["payload"])
                self.delivered_hashes.add(item["payload"]["hash"])
                self.log.append((item["payload"]["hash"], "delivered"))
            except Exception as exc:  # noqa: BLE001
                self.log.append((item["payload"]["hash"], f"failed:{exc}"))
                if item["attempts"] < self.max_attempts:
                    keep.append(item)
        self.pending = keep


def viewer_label(payload: dict[str, Any] | None, now: float) -> str:
    """수신 측 표시: 마지막 상태가 오래되면 '확인 지연'으로 구분."""
    if not payload:
        return "상태 기록 없음"
    if now - payload["generated_at"] > STALE_AFTER:
        return "확인 지연(마지막 기록 기준)"
    return "최신"
