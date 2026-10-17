import pytest

from app.normalizer import normalize_url


def test_many_spellings_become_one_url():
    variants = [
        "https://Example.com/pricing/",
        "example.com/pricing",
        "https://example.com:443/pricing?utm_source=x&fbclid=abc",
        "https://example.com//pricing#section",
    ]
    assert len({normalize_url(v) for v in variants}) == 1


def test_query_order_does_not_matter():
    assert normalize_url("https://a.com/?b=2&a=1") == normalize_url("https://a.com/?a=1&b=2")


def test_real_query_params_are_kept():
    assert "id=7" in normalize_url("https://a.com/item?id=7&utm_campaign=z")


@pytest.mark.parametrize("bad", ["ftp://a.com", "", "javascript:alert(1)", "https://"])
def test_rejects_bad_urls(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)
