from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import config


class WatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=3, max_length=2000)
    interval_minutes: int = 60
    sensitivity: Literal["low", "medium", "high"] = "medium"
    watch_selectors: list[str] = []
    ignore_selectors: list[str] = []
    keywords: list[str] = []
    notify_email: str | None = Field(default=None, max_length=200)
    digest: bool = False

    @field_validator("interval_minutes")
    @classmethod
    def _interval(cls, v: int) -> int:
        if not config.MIN_INTERVAL_MINUTES <= v <= config.MAX_INTERVAL_MINUTES:
            raise ValueError(
                f"Interval must be between {config.MIN_INTERVAL_MINUTES} minutes and 24 hours"
            )
        return v

    @field_validator("watch_selectors", "ignore_selectors", "keywords")
    @classmethod
    def _lists(cls, v: list[str]) -> list[str]:
        v = [s.strip() for s in v if s and s.strip()]
        if len(v) > 20 or any(len(s) > 300 for s in v):
            raise ValueError("At most 20 entries of 300 characters each")
        return v
