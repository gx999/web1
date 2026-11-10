"""국가법령정보센터 법령 본문 수집기.

실제 확인(2026-11-10): `/법령/<법령명>` 페이지는 1.3KB 내외의 껍데기이고, 본문은
iframe의 `lsInfoP.do?lsiSeq=..&efYd=..` → 내부 `lsInfoR.do` 호출로 불러온다.
`lsInfoR.do`를 직접 요청하면 키 없이 본문 HTML(약 500KB)을 받을 수 있다.
접근 제한·로그인·CAPTCHA가 나오면 우회하지 않고 그대로 보고한다.
"""
from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable

BASE = "https://www.law.go.kr"
Fetch = Callable[[str], tuple[int, bytes]]  # url -> (http_status, body)


@dataclass
class StatuteArticle:
    law_name: str
    article: str                 # 예: "제34조"
    status: str                  # retrieved | shell_only | access_restricted | not_found | transient_failed
    url: str
    lsi_seq: str | None = None
    effective_date: str | None = None   # 시행일(YYYYMMDD). 공포일·사건 당시 법과 구분
    text: str = ""
    sha256: str | None = None    # 받은 본문 전체의 해시(무결성 확인용, 진실성 판정 아님)
    note: str = ""


def _text(raw: bytes) -> str:
    s = raw.decode("utf-8", errors="ignore")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def resolve(law_name: str, fetch: Fetch) -> tuple[str | None, str | None, int]:
    url = f"{BASE}/{urllib.parse.quote('법령')}/{urllib.parse.quote(law_name)}"
    code, body = fetch(url)
    m = re.search(rb"lsInfoP\.do\?lsiSeq=(\d+)[^\"']*?efYd=(\d{8})", body)
    if not m:
        return None, None, code
    return m.group(1).decode(), m.group(2).decode(), code


def extract_article(full_text: str, article: str) -> str:
    """'제34조(' 로 시작해 다음 조문 머리('제NN조(' 또는 '제NN조의N(') 전까지."""
    start = full_text.find(article + "(")
    if start < 0:
        return ""
    nxt = re.compile(r"제\d+조(?:의\d+)?\(")
    for m in nxt.finditer(full_text, start + len(article) + 1):
        if m.group(0) != article + "(":
            return full_text[start:m.start()].strip()
    return full_text[start:start + 4000].strip()


def fetch_article(law_name: str, article: str, fetch: Fetch) -> StatuteArticle:
    try:
        seq, ef, code = resolve(law_name, fetch)
    except Exception as exc:  # noqa: BLE001
        return StatuteArticle(law_name, article, "transient_failed", BASE, note=str(exc)[:200])
    if code in (0, 401, 403, 451):
        return StatuteArticle(law_name, article, "access_restricted", BASE, note=f"http {code}")
    if not seq:
        return StatuteArticle(law_name, article, "not_found", BASE, note="본문 주소(lsiSeq)를 찾지 못함")
    url = (f"{BASE}/LSW/lsInfoR.do?lsiSeq={seq}&chrClsCd=010202&urlMode=lsInfoR"
           f"&efYd={ef}&ancYnChk=0")
    code, body = fetch(url)
    if code in (0, 401, 403, 451):
        return StatuteArticle(law_name, article, "access_restricted", url, seq, ef, note=f"http {code}")
    if code >= 500:
        return StatuteArticle(law_name, article, "transient_failed", url, seq, ef, note=f"http {code}")
    text = extract_article(_text(body), article)
    status = "retrieved" if text else "shell_only"
    return StatuteArticle(law_name, article, status, url, seq, ef, text,
                          hashlib.sha256(body).hexdigest(),
                          "" if text else "본문에서 해당 조문을 찾지 못함")


def urllib_fetch(url: str, timeout: int = 30) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research lab collector)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, b""  # 연결 거부·시간초과 (프로그램 오류는 그대로 예외로 올림)
