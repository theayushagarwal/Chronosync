"""Decide whether a fetch is a real page, an error, or a block.

The point: a site outage or a CAPTCHA wall is NOT a content change, and must
never be diffed, snapshotted, or alerted as one.
"""

BLOCK_STATUSES = {401, 403, 429, 451}
BLOCK_PHRASES = (
    "captcha",
    "verify you are human",
    "are you a robot",
    "are you human",
    "just a moment",
    "checking your browser",
    "access denied",
    "unusual traffic",
    "attention required",
)
# Only treat phrases as a block signal on short pages, so a blog post that
# merely mentions CAPTCHAs is not misclassified.
SHORT_PAGE_CHARS = 1500
MIN_REAL_CHARS = 20


def classify(status_code, text: str, error: str | None = None) -> tuple[str, str]:
    """Return (verdict, reason); verdict is 'ok', 'error' or 'blocked'."""
    if error:
        return "error", error
    if status_code is None:
        return "error", "No response received"
    if status_code in BLOCK_STATUSES:
        return "blocked", f"HTTP {status_code}: the site refused the request"
    if status_code >= 500:
        return "error", f"HTTP {status_code}: the site is having problems"
    if status_code >= 400:
        return "error", f"HTTP {status_code}: page not available"
    body = (text or "").strip()
    low = body.lower()
    if len(body) < SHORT_PAGE_CHARS and any(p in low for p in BLOCK_PHRASES):
        return "blocked", "Looks like a CAPTCHA or bot-check page"
    if len(body) < MIN_REAL_CHARS:
        return "error", "Page rendered almost no text"
    return "ok", ""
