"""FastAPI app: REST API, visual picker, RSS feeds and the static dashboard."""
import asyncio
import html as htmllib
import logging
import secrets
from contextlib import asynccontextmanager

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, differ, feeds
from .fetcher import Fetcher
from .models import WatchCreate
from .normalizer import assert_fetchable, domain_of, normalize_url
from .scheduler import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = config.BASE_DIR / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    app.state.fetcher = Fetcher()
    app.state.scheduler = Scheduler(app.state.fetcher)
    app.state.scheduler.start()
    yield
    await app.state.scheduler.stop()
    try:
        await asyncio.wait_for(app.state.fetcher.close(), 10)
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning("browser did not close in time")


app = FastAPI(title="Website Change Detector", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


# ---------------- watches ----------------

@app.post("/api/watches", status_code=201)
async def create_watch(body: WatchCreate, request: Request):
    try:
        url = normalize_url(body.url)
        await assert_fetchable(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    wid = db.add_watch(url, domain_of(url), body.model_dump())
    request.app.state.scheduler.wake()
    return db.get_watch(wid)


@app.get("/api/watches")
async def list_watches():
    return db.list_watches()


@app.delete("/api/watches/{watch_id}", status_code=204)
async def delete_watch(watch_id: int):
    if not db.delete_watch(watch_id):
        raise HTTPException(404, "Watch not found")


@app.post("/api/watches/{watch_id}/check", status_code=202)
async def check_now(watch_id: int, request: Request):
    if not db.request_check_now(watch_id):
        raise HTTPException(404, "Watch not found")
    request.app.state.scheduler.wake()
    return {"queued": True}


def _watch_or_404(watch_id: int) -> dict:
    w = db.get_watch(watch_id)
    if not w:
        raise HTTPException(404, "Watch not found")
    return w


@app.get("/api/watches/{watch_id}/versions")
async def versions(watch_id: int):
    w = _watch_or_404(watch_id)
    return db.list_snapshots(w["page_id"])


@app.get("/api/watches/{watch_id}/diff")
async def text_diff(watch_id: int, a: int, b: int):
    """Diff of this watch's region between any two versions."""
    w = _watch_or_404(watch_id)
    sa, sb = db.get_snapshot(a), db.get_snapshot(b)
    if not sa or not sb or sa["page_id"] != w["page_id"] or sb["page_id"] != w["page_id"]:
        raise HTTPException(404, "Version not found for this watch")
    old, new = differ.watch_text(sa["html"], w), differ.watch_text(sb["html"], w)
    stats = differ.change_stats(old, new)
    return {
        "blocks": differ.diff_blocks(old, new),
        "old_text": old,
        "new_text": new,
        "added": len(stats["added"]),
        "removed": len(stats["removed"]),
        "a": {"id": sa["id"], "created_at": sa["created_at"]},
        "b": {"id": sb["id"], "created_at": sb["created_at"]},
    }


@app.get("/api/watches/{watch_id}/alerts")
async def watch_alerts(watch_id: int):
    _watch_or_404(watch_id)
    return db.alerts_for_watch(watch_id)


@app.get("/api/watches/{watch_id}/metrics")
async def watch_metrics(watch_id: int):
    _watch_or_404(watch_id)
    history = db.get_metrics(watch_id)
    values = [m["value"] for m in history]
    stats = {
        "count": len(values),
        "latest": values[-1] if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "change": round(values[-1] - values[0], 2) if len(values) > 1 else 0.0,
        "change_pct": round(((values[-1] - values[0]) / values[0]) * 100, 1) if len(values) > 1 and values[0] else 0.0,
    }
    return {"metrics": history, "stats": stats}


@app.get("/api/alerts")
async def all_alerts(limit: int = Query(50, le=200)):
    return db.list_alerts(limit)


# ---------------- screenshots ----------------

def _shot_bytes(snapshot_id: int) -> bytes:
    snap = db.get_snapshot(snapshot_id)
    if not snap or not snap["screenshot_path"]:
        raise HTTPException(404, "No screenshot for this version")
    try:
        with open(snap["screenshot_path"], "rb") as f:
            return f.read()
    except OSError:
        raise HTTPException(404, "Screenshot file is missing")


@app.get("/api/snapshots/{snapshot_id}/screenshot")
async def screenshot(snapshot_id: int):
    return Response(_shot_bytes(snapshot_id), media_type="image/png")


@app.get("/api/diff/screenshot")
async def screenshot_diff(a: int, b: int):
    png, changed = await asyncio.to_thread(differ.screenshot_diff, _shot_bytes(a), _shot_bytes(b))
    return Response(png, media_type="image/png", headers={"X-Changed-Fraction": f"{changed:.4f}"})


# ---------------- RSS ----------------

@app.get("/feeds/{watch_id}.xml")
async def rss(watch_id: int):
    w = _watch_or_404(watch_id)
    return Response(feeds.build_rss(w, db.alerts_for_watch(watch_id)), media_type="application/rss+xml")


# ---------------- visual picker ----------------

PICKER_JS = r"""
(function () {
  var mode = 'watch', last = null;
  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'mode') mode = e.data.mode;
  });
  function unique(sel) { try { return document.querySelectorAll(sel).length === 1; } catch (x) { return false; } }
  function pathFor(el) {
    var parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      if (el.id && unique('#' + CSS.escape(el.id))) { parts.unshift('#' + CSS.escape(el.id)); break; }
      var p = el.tagName.toLowerCase();
      if (el === document.body) { parts.unshift('body'); break; }
      var par = el.parentElement;
      if (par) {
        var same = Array.prototype.filter.call(par.children, function (c) { return c.tagName === el.tagName; });
        if (same.length > 1) p += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
      }
      parts.unshift(p);
      el = par;
    }
    return parts.join(' > ');
  }
  document.addEventListener('mouseover', function (e) {
    if (last) last.classList.remove('__cd_hover');
    last = e.target; last.classList.add('__cd_hover');
  }, true);
  document.addEventListener('click', function (e) {
    e.preventDefault(); e.stopPropagation();
    parent.postMessage({ type: 'pick', mode: mode, selector: pathFor(e.target),
      sample: (e.target.innerText || '').trim().slice(0, 80) }, '*');
  }, true);
})();
"""

PICKER_CSS = (
    ".__cd_hover{outline:2.5px solid #0071e3 !important;outline-offset:2px;"
    "box-shadow:0 0 14px rgba(0,113,227,0.5) !important;cursor:crosshair !important}"
)


@app.get("/api/picker", response_class=HTMLResponse)
async def picker(request: Request, url: str):
    """Serve a script-free copy of the rendered page with a click-to-select overlay.

    Shown inside a sandboxed iframe protected by a nonce CSP and frame-ancestors,
    so the target site's code can never run next to the dashboard.
    """
    try:
        norm = normalize_url(url)
        await assert_fetchable(norm)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    res = await request.app.state.fetcher.fetch(norm, screenshot=False)
    if res.error:
        raise HTTPException(502, f"Could not load the page: {res.error}")
    soup = BeautifulSoup(res.html, "html.parser")
    for t in soup(["script", "noscript", "iframe", "object", "embed", "meta"]):
        t.decompose()
    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.lower().startswith("on")]:
            del el[attr]
    nonce = secrets.token_urlsafe(12)
    head = soup.head or soup.new_tag("head")
    if soup.head is None:
        (soup.html or soup).insert(0, head)
    base = soup.new_tag("base", href=norm)
    style = soup.new_tag("style")
    style.string = PICKER_CSS
    script = soup.new_tag("script", nonce=nonce)
    script.string = PICKER_JS
    head.insert(0, base)
    head.append(style)
    (soup.body or soup).append(script)
    csp = (
        f"default-src 'none'; img-src * data: blob:; style-src * 'unsafe-inline'; "
        f"font-src * data:; script-src 'nonce-{nonce}'; "
        f"frame-ancestors 'self' http://localhost:* http://127.0.0.1:*"
    )
    return HTMLResponse(str(soup), headers={"Content-Security-Policy": csp})
