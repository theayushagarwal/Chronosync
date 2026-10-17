from app.classifier import classify

REAL_PAGE = "A real article about gardening. " * 80


def test_ok_page():
    assert classify(200, REAL_PAGE)[0] == "ok"


def test_server_error_is_error_not_change():
    assert classify(503, "Service unavailable")[0] == "error"


def test_rate_limit_and_forbidden_are_blocked():
    assert classify(429, "")[0] == "blocked"
    assert classify(403, "")[0] == "blocked"


def test_short_captcha_page_is_blocked_even_with_200():
    assert classify(200, "Just a moment... verify you are human")[0] == "blocked"


def test_long_article_mentioning_captcha_is_still_ok():
    assert classify(200, REAL_PAGE + " We discuss CAPTCHA design here.")[0] == "ok"


def test_network_error_and_empty_page():
    assert classify(None, "", "net::ERR_CONNECTION_REFUSED")[0] == "error"
    assert classify(200, "  ")[0] == "error"
