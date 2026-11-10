"""RISS 검색 결과에서 국내 학술논문 '서지 후보'를 뽑는다.

검색 결과 목록만 읽으므로 상태는 항상 search_only(서지 후보)다. 초록·원문 확인은
별도 단계에서 하고, 그 전에는 '확인된 선행연구'로 표시하지 않는다.
"""
from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass

SEARCH = "https://www.riss.kr/search/Search.do?query={q}&colName=re_a_kor"


@dataclass
class Candidate:
    title: str
    authors: str
    publisher: str
    year: str
    journal: str
    volume: str
    detail_url: str
    kci_listed: bool
    status: str = "search_only"


def search_url(query: str) -> str:
    return SEARCH.format(q=urllib.parse.quote(query))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def parse(body: bytes) -> list[Candidate]:
    s = body.decode("utf-8", errors="ignore")
    i = s.find('class="srchResultListW"')
    if i < 0:
        return []
    out: list[Candidate] = []
    for block in re.split(r"<li>\s*<span style=\"display:none;\">", s[i:])[1:]:
        t = re.search(r'<p class="title"><a href="([^"]+)">(.*?)</a></p>', block, re.S)
        if not t:
            continue
        etc = re.search(r'<p class="etc">(.*?)</p>', block, re.S)
        spans = re.findall(r"<span[^>]*>(.*?)</span>", etc.group(1), re.S) if etc else []
        vals = [_clean(x) for x in spans]
        while len(vals) < 5:
            vals.append("")
        out.append(Candidate(
            title=_clean(t.group(2)), authors=vals[0], publisher=vals[1], year=vals[2],
            journal=vals[3], volume=vals[4],
            detail_url="https://www.riss.kr" + html.unescape(t.group(1)),
            kci_listed='alt="KCI등재"' in block,
        ))
    return out
