"""Scheduler, job queue, retries, rate limits and the change-detection flow.

Queue design: `pages.next_check` IS the queue. The loop claims due pages
atomically (locked_until), runs them with bounded concurrency, and writes the
next due time back. No Redis/Celery: SQLite already gives durability, and a
crash only costs a lock timeout. Trade-off: this is a single-process design.
"""
import asyncio
import logging
import random
import time

from . import alerts, config, db, differ
from .classifier import classify
from .fetcher import FetchResult
from .summarizer import summarize

log = logging.getLogger(__name__)


class DomainLimiter:
    """Never hit the same domain more often than once per `gap` seconds (+ jitter)."""

    def __init__(self):
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, domain: str) -> None:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            wait = self._last.get(domain, 0) + config.DOMAIN_MIN_GAP_S + random.uniform(
                0, config.DOMAIN_JITTER_S
            ) - time.monotonic()
            if wait > 0 and domain in self._last:
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()


class Scheduler:
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.limiter = DomainLimiter()
        self._tasks: set[asyncio.Task] = set()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._runner: asyncio.Task | None = None

    # ----- lifecycle -----
    def start(self):
        self._runner = asyncio.create_task(self._loop())

    async def stop(self):
        """Shut down without ever hanging: cancel in-flight checks and bound every wait."""
        self._stop.set()
        self._wake.set()
        if self._runner:
            try:
                await asyncio.wait_for(self._runner, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._runner.cancel()
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    def wake(self):
        self._wake.set()

    async def _loop(self):
        last_digest = time.time()
        while not self._stop.is_set():
            try:
                free = config.MAX_CONCURRENT_CHECKS - len(self._tasks)
                if free > 0:
                    for page in db.claim_due_pages(free):
                        t = asyncio.create_task(self._run(page["id"]))
                        self._tasks.add(t)
                        t.add_done_callback(self._tasks.discard)
                if time.time() - last_digest >= config.DIGEST_INTERVAL_S:
                    last_digest = time.time()
                    await alerts.send_digests()
            except Exception:
                log.exception("scheduler loop error")
            try:
                await asyncio.wait_for(self._wake.wait(), config.TICK_S)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _run(self, page_id: int):
        try:
            await asyncio.wait_for(self.process_page(page_id), config.JOB_TIMEOUT_S)
        except asyncio.TimeoutError:
            page = db.get_page(page_id)
            if page:
                await self._failure(page, "error", "The check timed out")
        except Exception as exc:
            log.exception("check crashed")
            page = db.get_page(page_id)
            if page:
                await self._failure(page, "error", f"Internal error: {exc.__class__.__name__}")

    # ----- one check -----
    async def process_page(self, page_id: int):
        page = db.get_page(page_id)
        if not page:
            return
        if config.RESPECT_ROBOTS and not await self.fetcher.robots_allowed(page["url"]):
            return await self._failure(page, "blocked", "robots.txt does not allow checking this page")
        res, verdict, reason = await self._fetch_with_retries(page)
        if verdict != "ok":
            return await self._failure(page, verdict, reason)
        await self._success(page, res)

    async def _fetch_with_retries(self, page: dict):
        last = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            await self.limiter.wait(page["domain"])
            res = await self.fetcher.fetch(page["url"])
            verdict, reason = classify(res.status_code, res.text, res.error)
            if verdict in ("ok", "blocked"):  # never retry a block: that is hammering
                return res, verdict, reason
            last = (res, verdict, reason)
            if attempt < config.MAX_RETRIES:
                await asyncio.sleep(config.RETRY_BASE_S * 2 ** (attempt - 1))
        return last

    async def _failure(self, page: dict, status: str, reason: str):
        now = time.time()
        fails = page["consecutive_failures"] + 1
        delay = min(page["interval_s"] * 2 ** min(fails - 1, 5), config.MAX_BACKOFF_S)
        db.update_page(
            page["id"], status=status, last_error=reason, last_checked=now,
            consecutive_failures=fails, next_check=now + delay, locked_until=0,
        )
        # Alert once after N consecutive failures; the flag resets on the next success.
        if fails >= config.ERROR_ALERT_AFTER and not page["error_alerted"]:
            db.update_page(page["id"], error_alerted=1)
            for w in db.watches_for_page(page["id"]):
                msg = f"{fails} checks in a row failed: {reason}"
                aid = db.add_alert(w["id"], None, "error", msg, config.PUBLIC_BASE_URL)
                if aid:
                    await alerts.dispatch(aid)

    async def _success(self, page: dict, res: FetchResult):
        now = time.time()
        pid = page["id"]
        cleaned = differ.clean_text(res.text)
        h = differ.content_hash(cleaned)
        latest = db.latest_snapshot(pid)
        base = dict(
            status="ok", last_error=None, last_checked=now, consecutive_failures=0,
            error_alerted=0, locked_until=0,
        )
        jitter = random.uniform(0, min(60, page["interval_s"] * 0.1))
        regular_next = now + page["interval_s"] + jitter

        if latest is None:  # first look: store a baseline, alert on nothing
            sid = db.add_snapshot(pid, h, cleaned, res.html, res.screenshot)
            self._record_metrics(pid, sid, res.html)
            return db.update_page(pid, **base, last_changed=now, pending_hash=None, next_check=regular_next)

        if h == latest["content_hash"]:  # unchanged (or a blip that went away)
            return db.update_page(pid, **base, pending_hash=None, next_check=regular_next)

        if page["pending_hash"] != h:
            # First time we see this new content: don't trust it yet. Re-check soon.
            # A page that differs on every load never confirms, so it never alerts.
            return db.update_page(
                pid, **base, pending_hash=h, next_check=now + config.CONFIRM_DELAY_S
            )

        # Same new content seen on two consecutive checks: it's real.
        sid = db.add_snapshot(pid, h, cleaned, res.html, res.screenshot)
        self._record_metrics(pid, sid, res.html)
        db.update_page(pid, **base, last_changed=now, pending_hash=None, next_check=regular_next)
        new = db.get_snapshot(sid)
        await self._notify(page, latest, new)

    def _record_metrics(self, page_id: int, snapshot_id: int, html: str):
        for w in db.watches_for_page(page_id):
            t = differ.watch_text(html, w)
            num = differ.extract_number(t)
            if num is not None:
                db.add_metric(w["id"], snapshot_id, num)

    async def _notify(self, page: dict, old: dict, new: dict):
        for w in db.watches_for_page(page["id"]):
            old_t, new_t = differ.watch_text(old["html"], w), differ.watch_text(new["html"], w)
            if old_t == new_t:
                continue
            st = differ.change_stats(old_t, new_t)
            if st["changed_chars"] < config.SENSITIVITY_MIN_CHARS[w["sensitivity"]]:
                continue
            if w["keywords"] and not differ.keywords_hit(w["keywords"], st["added"], st["removed"]):
                continue
            summary = await summarize(w["name"], st["added"], st["removed"])
            link = f"{config.PUBLIC_BASE_URL}/#diff/{w['id']}/{old['id']}/{new['id']}"
            aid = db.add_alert(w["id"], new["id"], "change", summary, link, digest=w["digest"])
            if aid:
                await alerts.dispatch(aid)
