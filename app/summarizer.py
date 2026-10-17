"""One-line change summaries. Uses Groq if a key is set; otherwise a plain fallback."""
import logging

import httpx

from . import config

log = logging.getLogger(__name__)


def fallback_summary(added: list[str], removed: list[str]) -> str:
    head = (added or removed or [""])[0]
    head = head if len(head) <= 90 else head[:87] + "..."
    return f"{len(added)} line(s) added, {len(removed)} removed. First change: {head}"


async def summarize(watch_name: str, added: list[str], removed: list[str]) -> str:
    plain = fallback_summary(added, removed)
    if not config.GROQ_API_KEY:
        return plain
    changes = "ADDED:\n" + "\n".join(added[:15]) + "\nREMOVED:\n" + "\n".join(removed[:15])
    prompt = (
        f"A monitored web page called '{watch_name}' changed. In one short sentence "
        f"(max 25 words), say what changed. Be specific, no preamble.\n\n{changes[:2500]}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                json={
                    "model": config.GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 80,
                    "temperature": 0.2,
                },
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip().splitlines()[0]
            return text[:240] or plain
    except Exception as exc:  # never let a summary failure block an alert
        log.warning("AI summary failed, using fallback: %s", exc)
        return plain
