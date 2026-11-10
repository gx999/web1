"""비공개 정보 노출 검사. 공개 투영·원격 푸시 전에 호출한다.

정규식 탐지는 누락이 있을 수 있으므로 '통과'를 '안전 보증'으로 표현하지 않는다.
탐지되면 차단하고 사람 확인을 요구한다.
"""
from __future__ import annotations

import re

PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "kr_rrn": re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),                    # 주민등록번호 형태
    "kr_phone": re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
    "api_key": re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{10,})\b"),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
ALLOW = {
    "email": {"noreply@anthropic.com"},   # 커밋 서명용 공개 주소
}


def scan(text: str) -> list[tuple[str, str]]:
    hits = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            v = m.group(0)
            if v in ALLOW.get(name, set()):
                continue
            hits.append((name, v[:4] + "…"))  # 탐지값 전체를 다시 출력하지 않음
    return hits


def assert_clean(text: str, where: str) -> None:
    hits = scan(text)
    if hits:
        raise ValueError(f"{where}: 비공개 정보 의심 {len(hits)}건 {sorted({h[0] for h in hits})} — 사람 확인 필요")
