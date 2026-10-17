"""SQLite schema and queries.

Design: `pages` (one row per normalized URL) is separate from `watches` (one row
per user subscription). Fetching happens per page; alerting happens per watch.
That split is what makes 200 watchers of one URL cost one fetch.
`pages.next_check` doubles as the job queue (see scheduler.py).
"""
import contextlib
import json
import shutil
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages(
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  domain TEXT NOT NULL,
  interval_s INTEGER NOT NULL,
  next_check REAL NOT NULL DEFAULT 0,
  locked_until REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT,
  last_checked REAL,
  last_changed REAL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  error_alerted INTEGER NOT NULL DEFAULT 0,
  pending_hash TEXT
);
CREATE INDEX IF NOT EXISTS pages_due ON pages(next_check);

CREATE TABLE IF NOT EXISTS watches(
  id INTEGER PRIMARY KEY,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  interval_s INTEGER NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'medium',
  watch_selectors TEXT NOT NULL DEFAULT '[]',
  ignore_selectors TEXT NOT NULL DEFAULT '[]',
  keywords TEXT NOT NULL DEFAULT '[]',
  notify_email TEXT,
  digest INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS watches_page ON watches(page_id);

CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  content_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  html TEXT NOT NULL,
  screenshot_path TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_page ON snapshots(page_id, id);

CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY,
  watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  snapshot_id INTEGER,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  link TEXT,
  digest INTEGER NOT NULL DEFAULT 0,
  channels TEXT,
  created_at REAL NOT NULL,
  sent_at REAL,
  UNIQUE(watch_id, snapshot_id, kind)
);

CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY,
  watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE CASCADE,
  value REAL NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(watch_id, snapshot_id)
);
CREATE INDEX IF NOT EXISTS metrics_watch ON metrics(watch_id, created_at);
"""

PAGE_COLUMNS = {
    "interval_s", "next_check", "locked_until", "status", "last_error", "last_checked",
    "last_changed", "consecutive_failures", "error_alerted", "pending_hash",
}


@contextlib.contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SHOT_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        c.execute("UPDATE pages SET locked_until=0")  # recover from a crash mid-check
    backfill_metrics()


def _watch(row) -> dict:
    d = dict(row)
    for k in ("watch_selectors", "ignore_selectors", "keywords"):
        d[k] = json.loads(d[k] or "[]")
    d["digest"] = bool(d["digest"])
    return d


def _recompute_interval(c, page_id: int) -> None:
    row = c.execute("SELECT MIN(interval_s) m FROM watches WHERE page_id=?", (page_id,)).fetchone()
    if row["m"]:
        c.execute("UPDATE pages SET interval_s=? WHERE id=?", (row["m"], page_id))


# ---------- watches ----------

def add_watch(url: str, domain: str, w: dict) -> int:
    interval = int(w["interval_minutes"]) * 60
    with conn() as c:
        row = c.execute("SELECT id FROM pages WHERE url=?", (url,)).fetchone()
        if row:
            page_id = row["id"]
        else:
            page_id = c.execute(
                "INSERT INTO pages(url, domain, interval_s) VALUES(?,?,?)", (url, domain, interval)
            ).lastrowid
        wid = c.execute(
            """INSERT INTO watches(page_id,name,url,interval_s,sensitivity,watch_selectors,
               ignore_selectors,keywords,notify_email,digest,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                page_id, w["name"], url, interval, w.get("sensitivity", "medium"),
                json.dumps(w.get("watch_selectors", [])), json.dumps(w.get("ignore_selectors", [])),
                json.dumps(w.get("keywords", [])), w.get("notify_email"),
                1 if w.get("digest") else 0, time.time(),
            ),
        ).lastrowid
        _recompute_interval(c, page_id)
    return wid


def get_watch(watch_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    return _watch(row) if row else None


def watches_for_page(page_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM watches WHERE page_id=?", (page_id,)).fetchall()
    return [_watch(r) for r in rows]


def list_watches() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT w.*, p.status, p.last_error, p.last_checked, p.last_changed,
                      p.consecutive_failures,
                      (SELECT COUNT(*) FROM snapshots s WHERE s.page_id=p.id) AS versions,
                      (SELECT m.value FROM metrics m WHERE m.watch_id=w.id ORDER BY m.id DESC LIMIT 1) AS latest_value
               FROM watches w JOIN pages p ON p.id=w.page_id ORDER BY w.id DESC"""
        ).fetchall()
    return [_watch(r) for r in rows]


def delete_watch(watch_id: int) -> bool:
    with conn() as c:
        row = c.execute("SELECT page_id FROM watches WHERE id=?", (watch_id,)).fetchone()
        if not row:
            return False
        page_id = row["page_id"]
        c.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        left = c.execute("SELECT COUNT(*) n FROM watches WHERE page_id=?", (page_id,)).fetchone()["n"]
        if left == 0:
            c.execute("DELETE FROM pages WHERE id=?", (page_id,))
            shutil.rmtree(config.SHOT_DIR / str(page_id), ignore_errors=True)
        else:
            _recompute_interval(c, page_id)
    return True


# ---------- pages / queue ----------

def get_page(page_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    return dict(row) if row else None


def update_page(page_id: int, **fields) -> None:
    bad = set(fields) - PAGE_COLUMNS
    if bad:
        raise ValueError(f"Unknown page columns: {bad}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE pages SET {sets} WHERE id=?", (*fields.values(), page_id))


def claim_due_pages(limit: int) -> list[dict]:
    """Atomically claim pages whose check is due. Claim = push locked_until forward."""
    now = time.time()
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            "SELECT * FROM pages WHERE next_check<=? AND locked_until<=? ORDER BY next_check LIMIT ?",
            (now, now, limit),
        ).fetchall()
        for r in rows:
            c.execute("UPDATE pages SET locked_until=? WHERE id=?", (now + config.LOCK_S, r["id"]))
    return [dict(r) for r in rows]


def request_check_now(watch_id: int) -> bool:
    with conn() as c:
        row = c.execute("SELECT page_id FROM watches WHERE id=?", (watch_id,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE pages SET next_check=0, locked_until=0 WHERE id=?", (row["page_id"],))
    return True


# ---------- snapshots ----------

def latest_snapshot(page_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM snapshots WHERE page_id=? ORDER BY id DESC LIMIT 1", (page_id,)
        ).fetchone()
    return dict(row) if row else None


def get_snapshot(snapshot_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    return dict(row) if row else None


def list_snapshots(page_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT id, page_id, substr(content_hash,1,10) AS hash, created_at,
                      screenshot_path IS NOT NULL AS has_screenshot
               FROM snapshots WHERE page_id=? ORDER BY id DESC""",
            (page_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_snapshot(page_id: int, content_hash: str, text: str, html: str, screenshot: bytes | None) -> int:
    html = html[: config.MAX_HTML_BYTES]
    with conn() as c:
        sid = c.execute(
            "INSERT INTO snapshots(page_id,content_hash,text,html,created_at) VALUES(?,?,?,?,?)",
            (page_id, content_hash, text, html, time.time()),
        ).lastrowid
        if screenshot:
            folder = config.SHOT_DIR / str(page_id)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{sid}.png"
            path.write_bytes(screenshot)
            c.execute("UPDATE snapshots SET screenshot_path=? WHERE id=?", (str(path), sid))
        _prune(c, page_id)
    return sid


def _prune(c, page_id: int) -> None:
    rows = c.execute(
        "SELECT id, screenshot_path FROM snapshots WHERE page_id=? ORDER BY id DESC LIMIT -1 OFFSET ?",
        (page_id, config.MAX_SNAPSHOTS_PER_PAGE),
    ).fetchall()
    for r in rows:
        if r["screenshot_path"]:
            try:
                (config.SHOT_DIR.parent / r["screenshot_path"]).unlink(missing_ok=True)
            except OSError:
                pass
        c.execute("DELETE FROM snapshots WHERE id=?", (r["id"],))


# ---------- alerts ----------

def add_alert(watch_id: int, snapshot_id: int | None, kind: str, summary: str,
              link: str | None = None, digest: bool = False) -> int | None:
    """Insert an alert. Returns None if this (watch, snapshot, kind) alert already exists."""
    try:
        with conn() as c:
            return c.execute(
                """INSERT INTO alerts(watch_id,snapshot_id,kind,summary,link,digest,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (watch_id, snapshot_id, kind, summary, link, 1 if digest else 0, time.time()),
            ).lastrowid
    except sqlite3.IntegrityError:
        return None


def get_alert(alert_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    return dict(row) if row else None


def mark_alert_sent(alert_id: int, channels: str) -> None:
    with conn() as c:
        c.execute("UPDATE alerts SET sent_at=?, channels=? WHERE id=?", (time.time(), channels, alert_id))


def unsent_digest_alerts() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM alerts WHERE digest=1 AND sent_at IS NULL ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def list_alerts(limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT a.*, w.name AS watch_name FROM alerts a
               JOIN watches w ON w.id=a.watch_id ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def alerts_for_watch(watch_id: int, limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM alerts WHERE watch_id=? AND kind='change' ORDER BY id DESC LIMIT ?",
            (watch_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- metrics ----------

def add_metric(watch_id: int, snapshot_id: int | None, value: float, created_at: float | None = None) -> int | None:
    try:
        with conn() as c:
            return c.execute(
                "INSERT INTO metrics(watch_id, snapshot_id, value, created_at) VALUES(?,?,?,?)",
                (watch_id, snapshot_id, value, created_at or time.time()),
            ).lastrowid
    except sqlite3.IntegrityError:
        return None


def get_metrics(watch_id: int, limit: int = 200) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM metrics WHERE watch_id=? ORDER BY created_at ASC LIMIT ?",
            (watch_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def backfill_metrics() -> None:
    from . import differ
    try:
        with conn() as c:
            watches = c.execute("SELECT * FROM watches").fetchall()
            for w_row in watches:
                w = _watch(w_row)
                snapshots = c.execute(
                    "SELECT * FROM snapshots WHERE page_id=? ORDER BY id ASC", (w["page_id"],)
                ).fetchall()
                for s in snapshots:
                    exists = c.execute(
                        "SELECT 1 FROM metrics WHERE watch_id=? AND snapshot_id=?", (w["id"], s["id"])
                    ).fetchone()
                    if not exists:
                        t = differ.watch_text(s["html"], w)
                        num = differ.extract_number(t)
                        if num is not None:
                            c.execute(
                                "INSERT OR IGNORE INTO metrics(watch_id, snapshot_id, value, created_at) VALUES(?,?,?,?)",
                                (w["id"], s["id"], num, s["created_at"]),
                            )
    except Exception:
        pass

