"""Noise filtering, region extraction, text diff and screenshot diff."""
import difflib
import hashlib
import io
import re

from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageDraw

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
NOISE_PATTERNS = [
    # ISO-ish timestamps
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"),
    # clock times
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:am|pm)?\b", re.I),
    # "5 minutes ago"
    re.compile(r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b", re.I),
    re.compile(r"\b(?:just now|yesterday|today)\b", re.I),
    # "Jan 5, 2026" and "5 Jan 2026"
    re.compile(rf"\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\.?(?:,?\s+\d{{4}})?\b", re.I),
    # live counters: "1,234 views", "12 online"
    re.compile(
        r"\b[\d,.]+[km]?\s+(?:views?|visitors?|likes?|comments?|shares?|reads?|online|users?|viewing)\b",
        re.I,
    ),
    # long random-looking tokens (must contain a digit): CSRF tokens, hashes, session ids
    re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{24,}\b"),
]


def clean_text(text: str) -> str:
    """Strip volatile bits (timestamps, counters, tokens) and normalise whitespace per line."""
    out = []
    for line in (text or "").splitlines():
        for pat in NOISE_PATTERNS:
            line = pat.sub(" ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def content_hash(cleaned: str) -> str:
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def region_text(html: str, watch_selectors: list[str], ignore_selectors: list[str]) -> str:
    """Text of the watched regions (or the whole body), minus ignored regions."""
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    for sel in ignore_selectors:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            continue  # invalid selector: ignore rather than crash the check
    if watch_selectors:
        chunks = []
        for sel in watch_selectors:
            try:
                chunks += [el.get_text("\n", strip=True) for el in soup.select(sel)]
            except Exception:
                continue
        return "\n".join(chunks)
    return (soup.body or soup).get_text("\n", strip=True)


def watch_text(html: str, watch: dict) -> str:
    return clean_text(region_text(html, watch["watch_selectors"], watch["ignore_selectors"]))


def extract_number(text: str) -> float | None:
    """Extract the first prominent numerical or currency value from text."""
    if not text:
        return None
    match = re.search(r"(?:[\$€£₹¥]|USD|EUR|GBP|INR)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)", text)
    if match:
        raw = match.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def diff_blocks(old: str, new: str, context: int = 3) -> list[dict]:
    """Line diff as [{tag: equal|add|del|gap, text}], long equal runs collapsed."""
    a, b = old.splitlines(), new.splitlines()
    blocks: list[dict] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            run = a[i1:i2]
            if len(run) > context * 2 + 1:
                blocks += [{"tag": "equal", "text": t} for t in run[:context]]
                blocks.append({"tag": "gap", "text": f"{len(run) - context * 2} unchanged lines"})
                blocks += [{"tag": "equal", "text": t} for t in run[-context:]]
            else:
                blocks += [{"tag": "equal", "text": t} for t in run]
        else:
            blocks += [{"tag": "del", "text": t} for t in a[i1:i2]]
            blocks += [{"tag": "add", "text": t} for t in b[j1:j2]]
    return blocks


def change_stats(old: str, new: str) -> dict:
    added = [b["text"] for b in diff_blocks(old, new, 0) if b["tag"] == "add"]
    removed = [b["text"] for b in diff_blocks(old, new, 0) if b["tag"] == "del"]
    return {
        "added": added,
        "removed": removed,
        "changed_chars": sum(map(len, added)) + sum(map(len, removed)),
    }


def keywords_hit(keywords: list[str], added: list[str], removed: list[str]) -> bool:
    hay = " ".join(added + removed).lower()
    return any(k.lower() in hay for k in keywords)


def screenshot_diff(png_a: bytes, png_b: bytes, threshold: int = 30) -> tuple[bytes, float]:
    """Return (PNG with changed pixels highlighted over the newer shot, fraction changed)."""
    a = Image.open(io.BytesIO(png_a)).convert("RGB")
    b = Image.open(io.BytesIO(png_b)).convert("RGB")
    w, h = max(a.width, b.width), max(a.height, b.height)

    def pad(im: Image.Image) -> Image.Image:
        canvas = Image.new("RGB", (w, h), "white")
        canvas.paste(im, (0, 0))
        return canvas

    a, b = pad(a), pad(b)
    mask = ImageChops.difference(a, b).convert("L").point(lambda p: 255 if p > threshold else 0)
    changed = mask.histogram()[255] / float(w * h)
    red = Image.new("RGB", (w, h), (225, 40, 40))
    out = Image.composite(red, b, mask.point(lambda p: 130 if p else 0))
    box = mask.getbbox()
    if box:
        ImageDraw.Draw(out).rectangle(box, outline=(225, 40, 40), width=3)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue(), changed
