"""비공개 정보 탐지 모의시험. 아래 값은 모두 가짜(형식만 맞춘 값)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kernel.privacy import assert_clean, scan  # noqa: E402
from kernel.projection import assert_public_safe  # noqa: E402

FAKE = {
    "email": "someone@example.org",
    "kr_rrn": "900101-1000000",
    "kr_phone": "010-0000-0000",
    "api_key": "sk-" + "A" * 24,
    "private_key": "-----BEGIN RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("kind,value", FAKE.items())
def test_detects(kind, value):
    hits = scan(f"본문 {value} 끝")
    assert kind in {h[0] for h in hits}
    assert value not in str(hits)          # 탐지값 전체를 되풀이하지 않음


def test_allows_commit_signature_and_plain_text():
    assert scan("Co-Authored-By: Claude <noreply@anthropic.com>") == []
    assert scan("개인정보 보호법 제34조 시행 2026. 9. 11.") == []


def test_assert_clean_blocks():
    with pytest.raises(ValueError):
        assert_clean("연락처 010-0000-0000", "공개 투영")


def test_public_projection_shape_still_enforced():
    with pytest.raises(ValueError):
        assert_public_safe({"generated_at": 0, "roles": [{"role": "x", "state": "idle", "title": "원고"}], "counts": {}, "hash": "h"})
