"""Headless-browser fetching (Playwright) plus a robots.txt cache.

Each fetch uses a brand-new browser context: no cookies, no stored
credentials, nothing carried between checks.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import config
from .normalizer import host_is_public

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    status_code: int | None = None
    text: str = ""
    html: str = ""
    screenshot: bytes | None = None
    error: str | None = None


class RobotsCache:
    """Per-origin robots.txt, cached for an hour. If it cannot be fetched we allow."""

    TTL = 3600

    def __init__(self):
        self._cache: dict[str, tuple[float, RobotFileParser | None]] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        hit = self._cache.get(origin)
        if not hit or time.time() - hit[0] > self.TTL:
            parser = None
            try:
                async with httpx.AsyncClient(timeout=5, follow_redirects=True) as c:
                    r = await c.get(origin + "/robots.txt", headers={"User-Agent": config.USER_AGENT})
                if r.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(r.text.splitlines())
            except Exception as exc:
                log.info("robots.txt unavailable for %s: %s", origin, exc)
            hit = (time.time(), parser)
            self._cache[origin] = hit
        parser = hit[1]
        return True if parser is None else parser.can_fetch(config.ROBOTS_TOKEN, url)


class Fetcher:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._start_lock = asyncio.Lock()
        self.robots = RobotsCache()
        self._host_ok: dict[str, bool] = {}

    async def _ensure_browser(self):
        async with self._start_lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright  # lazy: tests don't need it

                if self._pw is None:
                    self._pw = await async_playwright().start()
                try:
                    self._browser = await self._pw.chromium.launch(headless=True)
                except Exception:
                    # e.g. `playwright install chromium` was never run. Tear the driver
                    # down so a failed launch cannot leave background tasks behind.
                    try:
                        await self._pw.stop()
                    finally:
                        self._pw = None
                    raise
        return self._browser

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def robots_allowed(self, url: str) -> bool:
        return await self.robots.allowed(url)

    async def _route_guard(self, route):
        """Abort navigations (incl. redirects) that land on private addresses."""
        req = route.request
        if config.ALLOW_PRIVATE_HOSTS or not req.is_navigation_request():
            return await route.continue_()
        host = urlsplit(req.url).hostname or ""
        if host not in self._host_ok:
            self._host_ok[host] = await asyncio.to_thread(host_is_public, host)
        if self._host_ok[host]:
            return await route.continue_()
        return await route.abort()

    async def fetch(self, url: str, screenshot: bool = True) -> FetchResult:
        context = None
        try:
            from playwright.async_api import TimeoutError as PWTimeout

            browser = await self._ensure_browser()
            context = await browser.new_context(
                user_agent=config.USER_AGENT, viewport={"width": 1280, "height": 800}
            )
            await context.route("**/*", self._route_guard)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_S * 1000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                pass  # chatty pages never go idle; what we have is good enough
            text = await page.inner_text("body")
            html = await page.content()
            shot = await page.screenshot(full_page=True, type="png") if screenshot else None
            return FetchResult(response.status if response else None, text, html, shot)
        except Exception as exc:
            msg = str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__
            return FetchResult(error=msg)
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
