"""KCI 논문 검색 → '서지 후보'.

실제 확인(2026-11-10): GET `?sereArticleSearchBean.keyword=` 는 검색어를 무시하고 최신 무관
논문(예: 혈소판 연구)을 돌려준다. POST `poSearchBean.queryText`를 써야 하며, 그래도
결과마다 검색어 관련성을 검사해 무관 결과가 선행연구로 섞이지 않게 한다.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

SEARCH = "https://www.kci.go.kr/kciportal/po/search/poArtiSearList.kci"
DETAIL = "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId={}"


@dataclass
class KciCandidate:
    arti_id: str
    title: str
    authors: list[str]
    publisher: str
    journal: str
    issue_date: str          # 발행 연월(YYYY.MM) — 게재확정일·온라인공개일과 다를 수 있음
    kci_listed: bool
    relevant: bool           # 검색어 중 하나 이상이 제목에 있음
    url: str
    status: str = "search_only"


def form(query: str, rows: int = 20) -> dict[str, str]:
    return {"poSearchBean.printKeyword": query, "poSearchBean.queryText": query,
            "poSearchBean.docsCount": str(rows), "poSearchBean.startPg": "1"}


def _c(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def parse(body: bytes, query: str) -> list[KciCandidate]:
    s = body.decode("utf-8", errors="ignore")
    terms = [t for t in re.split(r"\s+", query.strip()) if len(t) >= 2]
    out = []
    rows = re.split(r'<td class="checkpoint">', s)[1:]
    for row in rows:
        m = re.search(r'artiId=(ART\d+)"[^>]*class="subject">(.*?)</a>', row, re.S)
        if not m:
            continue
        info = re.search(r'<ul class="nopm floats subject-info">(.*?)</ul>', row, re.S)
        lis = re.findall(r"<li>(.*?)</li>", info.group(1), re.S) if info else []
        authors = [_c(a) for li in lis if "poCretDetail" in li for a in re.findall(r"<a[^>]*>(.*?)</a>", li, re.S)]
        pub = next((_c(li) for li in lis if "poInsiSearSoceView" in li), "")
        jour = next((_c(li) for li in lis if "ciSereInfoView" in li), "")
        date = next((_c(li) for li in lis if re.fullmatch(r"\s*\d{4}\.\d{2}\s*", _c(li) or "x")), "")
        title = _c(m.group(2))
        out.append(KciCandidate(
            arti_id=m.group(1), title=title, authors=authors, publisher=pub, journal=jour,
            issue_date=date, kci_listed="KCI 등재" in row[:600],
            relevant=any(t in title for t in terms), url=DETAIL.format(m.group(1))))
    return out


def post_fetch(url: str, data: dict[str, str], timeout: int = 30) -> tuple[int, bytes]:
    import urllib.error
    import urllib.parse
    import urllib.request
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"User-Agent": "Mozilla/5.0 (research lab collector)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, b""
