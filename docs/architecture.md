# Architecture

```mermaid
flowchart TD
    UI[Dashboard + REST API] -->|add watch| W[(watches)]
    W --> P[(pages: one row per normalized URL)]
    S[Scheduler loop] -->|claim due pages| P
    S --> R{robots.txt ok?}
    R -- no --> F[failure: blocked]
    R -- yes --> L[Domain limiter: gap + jitter]
    L --> B[Playwright: fresh context]
    B --> C{classify}
    C -- error/blocked --> F
    F --> BO[Backoff + alert once after N failures]
    C -- ok --> N[clean text, hash]
    N --> H{hash vs last version}
    H -- same --> D1[clear pending, schedule next]
    H -- differs, first time --> D2[hold as pending, re-check soon]
    H -- differs, confirmed --> SN[(snapshots: text, html, screenshot)]
    SN --> E[evaluate EACH watch: region, sensitivity, keywords]
    E --> A[(alerts, UNIQUE per watch+version)]
    A --> CH[Discord / Telegram / Email / digest / RSS]
```

## Data model

- `pages`: one row per normalized URL. Holds status, timings, failure counters and the pending hash. `next_check` is the queue.
- `watches`: one row per subscription. Holds interval, sensitivity, selectors, keywords and notification settings.
- `snapshots`: a new version only when confirmed content changes.
- `alerts`: `UNIQUE(watch_id, snapshot_id, kind)` guarantees one alert per change per watcher.

## Why these choices

- **pages vs watches**: fetch cost scales with distinct URLs, not users.
- **DB-backed queue**: durable, inspectable, and no extra service. Limit: one process.
- **Persistence check over double-fetching**: confirming on the next scheduled check adds no extra load on the target site.
