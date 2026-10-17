"""Alert delivery. Dedup lives in db.add_alert (UNIQUE watch+snapshot+kind)."""
import asyncio
import logging
import smtplib
from collections import defaultdict
from email.message import EmailMessage

import httpx

from . import config, db

log = logging.getLogger(__name__)


def _format(watch: dict, alert: dict) -> str:
    icon = "Change detected" if alert["kind"] == "change" else "Problem checking page"
    link = f"\n{alert['link']}" if alert.get("link") else ""
    return f"{icon}: {watch['name']}\n{watch['url']}\n{alert['summary']}{link}"


async def _discord(msg: str) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        (await c.post(config.DISCORD_WEBHOOK_URL, json={"content": msg[:1900]})).raise_for_status()


async def _telegram(msg: str) -> None:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as c:
        (await c.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg[:3900]})).raise_for_status()


def _smtp(to: str, subject: str, body: str) -> None:
    m = EmailMessage()
    m["From"] = config.SMTP_FROM or config.SMTP_USER
    m["To"] = to
    m["Subject"] = subject
    m.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(m)


async def _send(watch: dict, subject: str, body: str) -> list[str]:
    """Try every configured channel. A failing channel is logged, never fatal."""
    sent = []
    jobs = []
    if config.DISCORD_WEBHOOK_URL:
        jobs.append(("discord", _discord(body)))
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        jobs.append(("telegram", _telegram(body)))
    if watch.get("notify_email") and config.SMTP_HOST:
        jobs.append(("email", asyncio.to_thread(_smtp, watch["notify_email"], subject, body)))
    for name, coro in jobs:
        try:
            await coro
            sent.append(name)
        except Exception as exc:
            log.warning("Alert channel %s failed: %s", name, exc)
    return sent


async def dispatch(alert_id: int) -> None:
    alert = db.get_alert(alert_id)
    if not alert or alert["sent_at"]:
        return
    watch = db.get_watch(alert["watch_id"])
    if not watch:
        return
    if alert["digest"]:
        return  # held for the daily digest
    sent = await _send(watch, f"[Change Detector] {watch['name']}", _format(watch, alert))
    db.mark_alert_sent(alert_id, ",".join(sent + ["dashboard"]))


async def send_digests() -> None:
    """One message per watch summarising everything held since the last digest."""
    by_watch = defaultdict(list)
    for a in db.unsent_digest_alerts():
        by_watch[a["watch_id"]].append(a)
    for wid, items in by_watch.items():
        watch = db.get_watch(wid)
        if not watch:
            continue
        lines = [f"- {a['summary']}" + (f" ({a['link']})" if a.get("link") else "") for a in items]
        body = f"Daily digest for {watch['name']} ({watch['url']}):\n" + "\n".join(lines)
        sent = await _send(watch, f"[Change Detector] Daily digest: {watch['name']}", body)
        for a in items:
            db.mark_alert_sent(a["id"], ",".join(sent + ["dashboard"]))
