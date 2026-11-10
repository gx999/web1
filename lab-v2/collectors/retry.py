"""공식 자료 수집의 제한적 재시도와 실패 원인 구분.

- 재시도 대상: 연결 실패(0)·429·5xx. 최대 attempts회, 대기 backoff초(지수 증가). 접근 거부(401/403/451)·
  404는 재시도하지 않는다(우회 금지, 같은 요청 반복은 의미 없음).
- 최종 원인(cause):
  connect_failed  : 매번 연결 단계 실패(거부·시간초과·DNS). 일시 장애인지 차단인지 이 정보만으로는 구분 불가
  rate_limited    : 429가 끝까지 지속
  server_error    : 5xx가 끝까지 지속
  access_denied   : 401/403/451(재시도 없이 즉시)
  not_found       : 404
- 같은 실행 안에서 성공한 응답은 재사용(같은 법령 본문 반복 요청 방지).
"""
from __future__ import annotations

import time
from typing import Callable

Fetch = Callable[[str], tuple[int, bytes]]


def cause_of(code: int) -> str | None:
    if 200 <= code < 300:
        return None
    if code == 0:
        return "connect_failed"
    if code == 429:
        return "rate_limited"
    if code >= 500:
        return "server_error"
    if code in (401, 403, 451):
        return "access_denied"
    if code == 404:
        return "not_found"
    return f"http_{code}"


class RetryingFetch:
    def __init__(self, fetch: Fetch, attempts: int = 3, backoff: float = 3.0, sleep=time.sleep) -> None:
        self.fetch, self.attempts, self.backoff, self.sleep = fetch, attempts, backoff, sleep
        self.cache: dict[str, tuple[int, bytes]] = {}
        self.log: list[dict] = []          # 요청별 시도 기록(원장 출처 메모에 남김)

    def __call__(self, url: str) -> tuple[int, bytes]:
        if url in self.cache:
            return self.cache[url]
        tries = []
        code, body = 0, b""
        for i in range(self.attempts):
            code, body = self.fetch(url)
            tries.append(code)
            if cause_of(code) not in ("connect_failed", "rate_limited", "server_error"):
                break
            if i < self.attempts - 1:
                self.sleep(self.backoff * (2 ** i))
        self.log.append({"url": url, "tries": tries, "cause": cause_of(code)})
        if 200 <= code < 300:
            self.cache[url] = (code, body)
        return code, body

    def last_note(self) -> str:
        if not self.log:
            return ""
        x = self.log[-1]
        return f"시도 {len(x['tries'])}회 {x['tries']} → {x['cause'] or 'ok'}"
