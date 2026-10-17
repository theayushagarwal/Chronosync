import pytest

from app import config, db


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh DB per test, and never touch the network or a developer's real channels."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SHOT_DIR", tmp_path / "shots")
    for name in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_HOST", "GROQ_API_KEY"):
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "DOMAIN_MIN_GAP_S", 0)
    monkeypatch.setattr(config, "DOMAIN_JITTER_S", 0)
    monkeypatch.setattr(config, "RETRY_BASE_S", 0)
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    monkeypatch.setattr(config, "CONFIRM_DELAY_S", 0)
    monkeypatch.setattr(config, "RESPECT_ROBOTS", False)
    db.init_db()
