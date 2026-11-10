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
