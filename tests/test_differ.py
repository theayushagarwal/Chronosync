import io

from PIL import Image

from app import differ


def test_noise_is_stripped():
    a = "Welcome\nUpdated 2026-10-17T08:30:11Z\n1,204 views\nToken a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    b = "Welcome\nUpdated 2026-10-17T09:45:59Z\n9,871 views\nToken z9y8x7w6v5u4z9y8x7w6v5u4z9y8"
    assert differ.clean_text(a) == differ.clean_text(b) == "Welcome\nUpdated\nToken"


def test_real_changes_survive_cleaning():
    a = differ.clean_text("Price: $19.99\nFree delivery")
    b = differ.clean_text("Price: $24.99\nFree delivery")
    assert a != b
    st = differ.change_stats(a, b)
    assert st["added"] == ["Price: $24.99"] and st["removed"] == ["Price: $19.99"]


def test_ignore_selector_removes_region():
    html = "<body><div id='ad'>Buy now 50% off</div><p>Real content</p></body>"
    assert "Buy now" not in differ.region_text(html, [], ["#ad"])


def test_watch_selector_limits_scope_and_survives_bad_selector():
    html = "<body><p id='price'>$10</p><p>Other</p></body>"
    assert differ.region_text(html, ["#price"], []) == "$10"
    assert differ.region_text(html, ["((("], []) == ""  # invalid selector: no crash


def test_diff_blocks_collapse_long_equal_runs():
    old = "\n".join(f"line {i}" for i in range(30))
    new = old.replace("line 15", "CHANGED")
    tags = [b["tag"] for b in differ.diff_blocks(old, new)]
    assert "gap" in tags and "add" in tags and "del" in tags


def test_keywords():
    assert differ.keywords_hit(["price"], ["New Price: 5"], [])
    assert not differ.keywords_hit(["price"], ["Footer text"], [])


def _png(color, size=(100, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_screenshot_diff_detects_change():
    out, frac = differ.screenshot_diff(_png("white"), _png("black"))
    assert frac > 0.99 and out[:4] == b"\x89PNG"
    assert differ.screenshot_diff(_png("white"), _png("white"))[1] == 0
