# Chronosync

Autonomous web monitoring platform and visual regression studio built with **FastAPI**, **Playwright**, and **SQLite**. Monitors public web pages in a headless browser, alerts you to meaningful content and price changes, and suppresses noise (clocks, timestamps, ads, tokens, and bot challenges). Features a dashboard with an interactive visual element picker, side-by-side comparison studio, draggable visual swipe slider, and numerical price history tracking.

---

## Quick Start

```bash
make setup      # Creates venv, installs dependencies, Playwright Chromium, .env
make run        # Starts FastAPI server at http://localhost:8000
make test       # Runs 29 unit & flow tests in ~1.5s (no network or browser needed)
```

Or using Docker:
```bash
docker compose up --build
```

### 30-Second Demo Mode

Run three commands in separate terminals:

```bash
make demo-site   # Launches local demo site on :8001
make run-demo    # Launches Change Detector app with short polling intervals
make seed        # Seeds 4 demo watches
```

1. Open **http://localhost:8000** to view the live dashboard.
2. Visit **http://localhost:8001/bump** to trigger a simulated price and text change.
3. Within ~10 seconds, the dashboard will highlight the changed row, update the version timeline, and stream an alert to the Activity Feed.
4. Other demo targets demonstrate noise handling:
   - `/flaky`: Content changes on every refresh and is ignored (never alerts).
   - `/captcha`: Classified as **blocked** (bot challenge, no retrying).
   - `/down`: Classified as **error** (500 status, failing N attempts).

---

## Dashboard UI/UX Architecture

The dashboard is built with vanilla JavaScript and a single, modern CSS design system adhering to strict accessibility (WCAG AA), motion tokens, and responsive layout standards.

### Dedicated View Architecture

Navigate smoothly via top tabs or single-key shortcuts (`1`, `2`, `3`, `4` / `N`):

- **Overview (`1`):** Executive KPI metrics (`Active Monitors`, `Healthy & OK`, `Recorded Versions`, `Blocked / Errors`), live **Activity Feed** with instant Changes/Errors filter pills and "New" visit badges, and quick-access monitor roster cards.
- **Monitors (`2`):** Full-width high-density table displaying Target Page, Status pills (`ok`, `error`, `blocked`, `pending`), exact time `<time>` tooltips, interval, change counts (`Baseline only` when 1), and actions (`Check`, `Inspect`, `Remove`). Below 900px, rows automatically convert into touch-optimized mobile cards. Includes instant search (`/`), sort dropdown, and status filter pills.
- **Diff Studio (`3`):** Visual comparison suite with deep link support (`#diff/<watch>/<a>/<b>`):
  - **Interactive Visual Slider:** Split-view draggable slider comparing snapshots.
  - **Unified Text Diff:** Formatted additions (green) and deletions (red) with line-count metrics and visual legend.
  - **Pixel Heatmap Diff:** Modified regions tinted over the latest snapshot.
  - **Side-by-Side View:** Synchronized snapshots with click-to-enlarge Lightbox modal.
  - **Price & Metric History:** SVG line chart and historical timeline tracking numerical prices and values.
  - **Plain-Language Status:** Human-readable explanations for blocked, error, and pending states with collapsible technical server logs (`<details id="tech-details">`).
  - **Copy Link Button:** One-click `#copy-diff-btn` to share the exact comparison.
- **Add Page (`4` or `N`):** Clean page registration form with inline validation, collapsible **Advanced Options** disclosure, and sandboxed **Visual Element Picker** positioned first.

### Key Capabilities & Polish

- **Interactive Summary Strip:** 5 client-side filter chips (**Total**, **Healthy**, **Failing**, **Blocked**, and **Changed in 24h**) with smooth count-up animations and bidirectional filter sync.
- **Reliable Keyed Updates:** Table rows and alerts reconcile in-place without `innerHTML` wipes, preserving scroll position, focus, and selection.
- **Polite Toast & Offline Banner:** Graceful connection loss banner (`#connection-banner`) and `aria-live` toast notifications.
- **Accessible In-Page Modals:** Native `<dialog>` elements for Remove confirmation (`#remove-dialog`), Visual Picker (`#picker-dialog`), Lightbox (`#lightbox-dialog`), and Keyboard Shortcuts (`#shortcuts-dialog`).
- **Design Tokens & System Typography:** Native SF Pro / system font stack, tabular numerals on all metrics and dates, double-ring focus indicators, and light/dark theme switcher with system preference detection.

### Keyboard Shortcuts

| Key | Action |
|---|---|
| `1` | Switch to Overview |
| `2` | Switch to Monitored Pages |
| `3` | Switch to Diff Studio |
| `4` or `N` | Switch to Add Page |
| `/` | Focus search filter input |
| `T` | Toggle Dark / Light theme |
| `R` | Refresh data feeds |
| `?` | Open keyboard shortcuts modal |
| `Esc` | Close any active dialog / blur inputs |

---

## Core Design Solutions

### 1. Is a site outage or CAPTCHA a change? No.
Every fetch is classified first (`app/classifier.py`) as `ok`, `error`, or `blocked`. Only `ok` results are diffed or stored. HTTP 5xx, timeouts, and empty pages are marked as `error`; 401/403/429 and short bot-check pages are marked as `blocked`. Outages never create a snapshot or false change alert. After `ERROR_ALERT_AFTER` (default 3) consecutive failures, a single health alert is sent. Blocked targets are never retried automatically to prevent IP bans.

### 2. If 200 users watch the same URL, is it fetched 200 times? No, once.
URLs are normalized (`app/normalizer.py`: scheme, default ports, case, tracking parameters, fragment removal, query ordering, trailing slash) so multiple watches map to a single record in `pages`. The scheduler fetches the page once, then evaluates each watch independently against its specific CSS selectors, sensitivity threshold, and keywords. The fetch interval is set to the shortest interval among all watchers.

### 3. How do we avoid false alerts on dynamic pages?
Four layers of suppression:
1. **Noise filter:** `differ.clean_text` strips dynamic timestamps, live relative times, dates, random CSRF tokens, and counters before hashing.
2. **Persistence check:** A changed content hash is held as `pending_hash` and verified again after `CONFIRM_DELAY_S`. It only becomes a recorded version and alert if identical content appears on two consecutive checks. Pages with random content on every load never confirm.
3. **Sensitivity thresholds:** Minimum changed character count (High: 1 char, Medium: ~20 chars, Low: ~200 chars).
4. **Targeted selectors:** Users can isolate specific elements to watch or ignore using CSS selectors or the sandboxed Visual Element Picker.

### 4. How do we prevent hammering sites?
- Per-domain minimum gap with random jitter (`DOMAIN_MIN_GAP_S`, default 10s) enforced across all pages on the same host.
- Bounded global concurrency (`MAX_CONCURRENT_CHECKS`).
- `robots.txt` compliance and honest `User-Agent` headers.
- Cadence clamping between 15 minutes and 24 hours.
- Exponential backoff on retries; blocked sites back off up to 24 hours.

---

## Requirements Map

| # | Requirement | Implementation |
|---|---|---|
| 1 | URL, name, interval; headless render | `app/models.py`, `app/fetcher.py` |
| 2 | Scheduler + queue, timeouts, retries; public pages only | `app/scheduler.py` |
| 3 | Text + screenshot per check; new snapshot only on hash change | `app/scheduler.py`, `app/db.py` |
| 4 | Noise filtering + sensitivity | `app/differ.py`, `app/config.py` |
| 5 | Visual picker saved as CSS selectors (watch and ignore) | `app/main.py` (`/api/picker`), `app/static/` |
| 6 | Text diff, highlighted screenshot diff, side-by-side | `app/differ.py`, `app/static/app.js` |
| 7 | Email, Telegram, Discord; deduped; summary + diff link; daily digest | `app/alerts.py` |
| 8 | Version timeline + dashboard (last checked/changed, status) | `app/static/`, `app/db.py` |

**Bonus Features Included:**
- Keyword-filtered alerts.
- Optional AI one-line summaries (`app/summarizer.py` with Groq API).
- Per-page RSS feeds (`/feeds/{id}.xml`).
- Numerical trend tracking & SVG metric charts.
- Chrome browser extension (`extension/`).

---

## Architecture & Data Flow

```
   ┌─────────────────────────────────────────────────────────┐
   │                     FastAPI Engine                      │
   │                                                         │
   │  POST /api/watches ──> Normalizer ──> SQLite (Pages)    │
   │                                            │            │
   │  Scheduler Loop <── Atomic Claim Due ──────┘            │
   │       │                                                 │
   │  Playwright Fetcher ──> Classifier (ok / err / blocked) │
   │                               │                         │
   │                               ▼ [ok only]               │
   │       ┌───────────────────────────────────────┐         │
   │       │ Noise Filter + Persistence Verifier   │         │
   │       └───────────────────────────────────────┘         │
   │                               │                         │
   │       ┌───────────────────────┴───────────────────┐     │
   │       ▼                                           ▼     │
   │  SQLite Snapshots                           Diff Studio │
   │  & Alerts Dispatch                     (Slider/Text/SVG)│
   └─────────────────────────────────────────────────────────┘
```

The database (`pages.next_check`) functions as a durable, transaction-safe job queue in SQLite without needing Redis or external broker dependencies.

---

## Configuration

All configuration is optional with safe defaults; see `.env.example`:

- `ALLOW_PRIVATE_HOSTS=0` (enforces strict SSRF protection; set to 1 in demo mode)
- `DISCORD_WEBHOOK_URL` (optional Discord alert push)
- `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID` (optional Telegram alert push)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (optional email delivery)
- `GROQ_API_KEY` (optional AI one-line diff summary)

---

## Testing

Run the test suite with:

```bash
make test
# or
pytest
```

All **29 unit and integration flow tests** pass in ~1.5s without requiring external network access or running browser instances.
