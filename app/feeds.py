"""Per-watch RSS feed."""
from email.utils import formatdate
from xml.sax.saxutils import escape

from . import config


def build_rss(watch: dict, alerts: list[dict]) -> str:
    items = "".join(
        f"<item><title>{escape(a['summary'][:120])}</title>"
        f"<link>{escape(a.get('link') or watch['url'])}</link>"
        f"<guid isPermaLink='false'>change-{a['id']}</guid>"
        f"<pubDate>{formatdate(a['created_at'])}</pubDate>"
        f"<description>{escape(a['summary'])}</description></item>"
        for a in alerts
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
        f"<title>{escape(watch['name'])}: changes</title>"
        f"<link>{escape(config.PUBLIC_BASE_URL)}</link>"
        f"<description>Changes detected on {escape(watch['url'])}</description>{items}</channel></rss>"
    )
