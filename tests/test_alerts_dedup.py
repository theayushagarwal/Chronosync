from app import db


def _watch():
    return db.add_watch("https://a.com/", "a.com", {"name": "A", "interval_minutes": 60})


def test_same_change_alerts_once():
    wid = _watch()
    assert db.add_alert(wid, 5, "change", "Price changed") is not None
    assert db.add_alert(wid, 5, "change", "Price changed") is None  # duplicate blocked


def test_new_snapshot_is_a_new_alert():
    wid = _watch()
    assert db.add_alert(wid, 5, "change", "x") and db.add_alert(wid, 6, "change", "y")


def test_two_watchers_share_one_page():
    w1 = db.add_watch("https://a.com/", "a.com", {"name": "one", "interval_minutes": 60})
    w2 = db.add_watch("https://a.com/", "a.com", {"name": "two", "interval_minutes": 15})
    assert db.get_watch(w1)["page_id"] == db.get_watch(w2)["page_id"]
    assert db.get_page(db.get_watch(w1)["page_id"])["interval_s"] == 15 * 60  # shortest wins
