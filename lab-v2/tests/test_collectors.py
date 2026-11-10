"""수집기 모의시험 — 실제 응답 구조를 본뜬 고정 입력. 실제 수집 결과가 아니다."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collectors import law_go_kr, riss  # noqa: E402

SHELL = ('<html><title>개인정보보호법</title><iframe src="/LSW//lsInfoP.do?lsiSeq=283839&amp;'
         'chrClsCd=010202&amp;urlMode=lsInfoP&amp;efYd=20260911&amp;ancYnChk=0"></iframe></html>').encode()
BODY = ("<div>제33조(개인정보 영향평가) ① 내용 </div><div>제34조(개인정보 유출등의 통지ㆍ신고) ① 개인정보처리자는 "
        "개인정보가 유출등이 되었음을 알게 되었을 때에는 지체 없이 ... </div>"
        "<div>제34조의2(노출된 개인정보의 삭제ㆍ차단) ① 내용</div>").encode()


def fake(routes):
    def f(url):
        for k, v in routes.items():
            if k in url:
                return v
        return 404, b""
    return f


def test_statute_article_retrieved_with_effective_date():
    a = law_go_kr.fetch_article("개인정보보호법", "제34조", fake({"%EB%B2%95%EB%A0%B9": (200, SHELL), "lsInfoR.do": (200, BODY)}))
    assert a.status == "retrieved" and a.lsi_seq == "283839" and a.effective_date == "20260911"
    assert a.text.startswith("제34조(개인정보 유출등의 통지ㆍ신고)") and "제34조의2" not in a.text
    assert a.sha256


def test_statute_shell_without_article_is_not_retrieved():
    a = law_go_kr.fetch_article("개인정보보호법", "제34조",
                                fake({"%EB%B2%95%EB%A0%B9": (200, SHELL), "lsInfoR.do": (200, b"<html>loading</html>")}))
    assert a.status == "shell_only"


def test_statute_access_restricted_not_bypassed():
    a = law_go_kr.fetch_article("개인정보보호법", "제34조", fake({"%EB%B2%95%EB%A0%B9": (403, b"")}))
    assert a.status == "access_restricted"


RISS = '''<div class="srchResultListW"><ul>
<li> <span style="display:none;"><input/></span><div class="markW"><span class="mark"><img src="/k.png" alt="KCI등재"></span></div>
<div class="cont ml60"><p class="title"><a href="/search/detail/DetailView.do?p_mat_type=1a&amp;control_no=2c"><span class='highlight'>침해사고</span> <span class='highlight'>신고</span>의 실효성 제고를 위한 정보통신망법 개선 연구</a></p>
<p class="etc"><span class="writer"><a href="#">이태승</a></span><span class="assigned"><a href="#">한국정보보호학회</a></span><span>2023</span>
<span><a href="#">정보보호학회논문지</a></span><span><a href="#">Vol.33 No.5</a></span></p></div></li>
</ul></div>'''.encode()


def test_riss_candidates_are_search_only():
    c, = riss.parse(RISS)
    assert c.title == "침해사고 신고의 실효성 제고를 위한 정보통신망법 개선 연구"
    assert (c.authors, c.publisher, c.year, c.journal, c.volume) == ("이태승", "한국정보보호학회", "2023", "정보보호학회논문지", "Vol.33 No.5")
    assert c.kci_listed and c.status == "search_only"


def test_riss_empty_page():
    assert riss.parse(b"<html>no results</html>") == []


def test_program_error_is_transient_not_access_restricted():
    def boom(url):
        raise UnicodeEncodeError("ascii", "법", 0, 1, "x")
    a = law_go_kr.fetch_article("개인정보보호법", "제34조", boom)
    assert a.status == "transient_failed"


from collectors import kci  # noqa: E402

KCI_ROW = '''<td class="checkpoint"><input/></td><td>1 .</td><td><div class="s-state-type-ico type-ico1 ico">KCI 등재</div>
<a href="/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003316221"
 onclick="x" class="subject">개인정보 유출사고의 형사처벌과 행정처분에 대한 연구</a>
<ul class="nopm floats subject-info"><li><a href="/kciportal/po/citationindex/poCretDetail.kci?x">홍길동</a></li>
<li><a href="/kciportal/po/search/poInsiSearSoceView.kci?x">한국학회</a></li>
<li><a href="/kciportal/ci/seriesSearch/ciSereInfoView.kci?x">학회지</a></li><li>2026.03</li></ul></td>'''
KCI_OFFTOPIC = KCI_ROW.replace("ART003316221", "ART003383027").replace(
    "개인정보 유출사고의 형사처벌과 행정처분에 대한 연구", "Collagen 유도의 혈소판 항혈전 효과")


def test_kci_parse_and_relevance_guard():
    good, bad = kci.parse((KCI_ROW + KCI_OFFTOPIC).encode(), "개인정보 유출")
    assert good.arti_id == "ART003316221" and good.authors == ["홍길동"] and good.journal == "학회지"
    assert good.issue_date == "2026.03" and good.kci_listed and good.relevant
    assert not bad.relevant           # 검색어를 무시한 결과는 선행연구로 쓰지 않음
    assert good.status == bad.status == "search_only"


from collectors.retry import RetryingFetch  # noqa: E402


def _seq(*codes):
    calls = []

    def f(url):
        calls.append(url)
        c = codes[min(len(calls) - 1, len(codes) - 1)]
        return (c, SHELL) if "%EB%B2%95%EB%A0%B9" in url else (c, BODY)
    return f, calls


def test_transient_connect_failure_retried_then_succeeds():
    f, calls = _seq(0, 0, 200, 200)
    rf = RetryingFetch(f, attempts=3, sleep=lambda s: None)
    a = law_go_kr.fetch_article("개인정보보호법", "제34조", rf)
    assert a.status == "retrieved" and len(calls) == 4      # 법령 페이지 3회(2회 실패) + 본문 1회


def test_repeated_connect_failure_is_transient_with_cause_not_access_restricted():
    f, calls = _seq(0)
    rf = RetryingFetch(f, attempts=3, sleep=lambda s: None)
    a = law_go_kr.fetch_article("개인정보보호법", "제34조", rf)
    assert a.status == "transient_failed" and "connect_failed" in a.note and len(calls) == 3


def test_access_denied_is_not_retried():
    f, calls = _seq(403)
    a = law_go_kr.fetch_article("개인정보보호법", "제34조", RetryingFetch(f, sleep=lambda s: None))
    assert a.status == "access_restricted" and len(calls) == 1


def test_same_url_served_from_cache_within_run():
    f, calls = _seq(200)
    rf = RetryingFetch(f, sleep=lambda s: None)
    law_go_kr.fetch_article("개인정보보호법", "제34조", rf)
    law_go_kr.fetch_article("개인정보보호법", "제29조", rf)
    assert len(calls) == 2
