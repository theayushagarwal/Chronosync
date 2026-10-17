"""All tunables live here and can be overridden from .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "detector.db"
SHOT_DIR = DATA_DIR / "screenshots"

# Check intervals the task allows: 15 min to 24 h.
MIN_INTERVAL_MINUTES = _int("MIN_INTERVAL_MINUTES", 15)
MAX_INTERVAL_MINUTES = 1440

# Politeness / load control
MAX_CONCURRENT_CHECKS = _int("MAX_CONCURRENT_CHECKS", 3)
DOMAIN_MIN_GAP_S = _float("DOMAIN_MIN_GAP_S", 10.0)
DOMAIN_JITTER_S = _float("DOMAIN_JITTER_S", 5.0)
RESPECT_ROBOTS = _bool("RESPECT_ROBOTS", True)
USER_AGENT = os.getenv("USER_AGENT", "ChangeDetectorBot/1.0 (+personal monitoring tool)")
ROBOTS_TOKEN = "ChangeDetectorBot"

# Timeouts and retries
NAV_TIMEOUT_S = _int("NAV_TIMEOUT_S", 30)
MAX_RETRIES = _int("MAX_RETRIES", 3)
RETRY_BASE_S = _float("RETRY_BASE_S", 5.0)
JOB_TIMEOUT_S = _int("JOB_TIMEOUT_S", 180)
LOCK_S = JOB_TIMEOUT_S + 60
MAX_BACKOFF_S = 86400
TICK_S = 2.0

# Noise handling
CONFIRM_DELAY_S = _int("CONFIRM_DELAY_S", 120)  # a change must survive a 2nd check
ERROR_ALERT_AFTER = _int("ERROR_ALERT_AFTER", 3)  # consecutive failures before alerting
SENSITIVITY_MIN_CHARS = {"high": 1, "medium": 20, "low": 200}

# Storage
MAX_HTML_BYTES = 2_000_000
MAX_SNAPSHOTS_PER_PAGE = _int("MAX_SNAPSHOTS_PER_PAGE", 200)

# Security: refuse to fetch localhost / private networks (SSRF). Demo turns this on.
ALLOW_PRIVATE_HOSTS = _bool("ALLOW_PRIVATE_HOSTS", False)

# Alerts
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
DIGEST_INTERVAL_S = _int("DIGEST_INTERVAL_S", 86400)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")

# Optional AI summaries
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
