"""End-to-end check logic with a fake browser: no network, no Playwright."""
import asyncio

from app import db
from app.fetcher import FetchResult
from app.scheduler import Scheduler


def ok(text: str) -> FetchResult:
    html = "<html><body>" + "".join(f"<p>{l}</p>" for l in text.splitlines()) + "</body></html>"
    return FetchResult(200, text, html)


class FakeFetcher:
    def __init__(self, results):
        self.results, self.calls = list(results), 0

    async def robots_allowed(self, url):
        return True

    async def fetch(self, url, screenshot=True):
        self.calls += 1
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


PAGE = "Aurora Desk Lamp\nA warm dimmable lamp with a three year warranty.\n"


def run(results, watches, checks):
    """Create watches on one URL, run `checks` checks, return (page, fetcher)."""
    ids = [db.add_watch("https://a.com/", "a.com", w) for w in watches]
    pid = db.get_watch(ids[0])["page_id"]
    fetcher = FakeFetcher(results)

    async def go():
        sch = Scheduler(fetcher)
        for _ in range(checks):
            await sch.process_page(pid)

    asyncio.run(go())
    return db.get_page(pid), fetcher, ids


W = {"name": "w", "interval_minutes": 60, "sensitivity": "high"}


def test_confirmed_change_alerts_exactly_once():
    _, _, ids = run([ok(PAGE + "Price: $19.99"), ok(PAGE + "Price: $24.99")], [W], checks=4)
    alerts = db.alerts_for_watch(ids[0])
    assert len(alerts) == 1
    assert len(db.list_snapshots(db.get_watch(ids[0])["page_id"])) == 2


def test_page_that_changes_every_load_never_alerts():
    results = [ok(PAGE + f"Random {w}") for w in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")]
    page, _, ids = run(results, [W], checks=6)
    assert db.alerts_for_watch(ids[0]) == []
    assert len(db.list_snapshots(page["id"])) == 1


def test_timestamp_only_change_is_noise():
    page, _, ids = run(
        [ok(PAGE + "Updated 08:30:11"), ok(PAGE + "Updated 09:45:59")], [W], checks=4
    )
    assert db.alerts_for_watch(ids[0]) == []
    assert page["pending_hash"] is None


def test_outage_and_captcha_are_never_changes():
    bad = [FetchResult(503, "Service unavailable"), FetchResult(200, "Just a moment... verify you are human")]
    page, _, ids = run([ok(PAGE)] + bad, [W], checks=3)
    assert len(db.list_snapshots(page["id"])) == 1  # baseline only
    assert db.alerts_for_watch(ids[0]) == []
    assert page["status"] in ("error", "blocked") and page["consecutive_failures"] == 2


def test_error_alert_fires_once_after_threshold(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ERROR_ALERT_AFTER", 2)
    page, _, ids = run([FetchResult(503, "down")], [W], checks=5)
    errors = [a for a in db.list_alerts() if a["kind"] == "error"]
    assert len(errors) == 1 and page["error_alerted"] == 1


def test_two_watchers_cost_one_fetch_and_sensitivity_filters():
    medium = {"name": "m", "interval_minutes": 60, "sensitivity": "medium"}
    high = {"name": "h", "interval_minutes": 60, "sensitivity": "high"}
    results = [ok(PAGE + "Price: 19"), ok(PAGE + "Price: 24")]  # tiny change: 18 changed chars
    _, fetcher, ids = run(results, [medium, high], checks=3)
    assert fetcher.calls == 3  # one fetch per check, regardless of watcher count
    assert db.alerts_for_watch(ids[0]) == []  # medium ignores a tiny edit
    assert len(db.alerts_for_watch(ids[1])) == 1  # high catches it
