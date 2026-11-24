from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./tripwire.db"

    # Jev access
    gateway_api_key: str | None = None
    typesafe_api_key: str | None = None
    jev_model: str | None = None
    jev_base_url: str | None = None

    # Claude fallback reviewer
    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-5-5"

    # Policy thresholds
    low_confidence: float = 0.6
    destructive_threshold: float = 0.8
    off_task_threshold: float = 0.8

    @property
    def jev_configured(self) -> bool:
        return bool(self.gateway_api_key or self.typesafe_api_key)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL") or "sqlite:///./tripwire.db",
        gateway_api_key=os.getenv("AI_GATEWAY_API_KEY") or None,
        typesafe_api_key=os.getenv("TYPESAFE_API_KEY") or None,
        jev_model=os.getenv("JEV_MODEL") or None,
        jev_base_url=os.getenv("JEV_BASE_URL") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        claude_model=os.getenv("CLAUDE_MODEL") or "claude-sonnet-5-5",
        low_confidence=_float("TRIPWIRE_LOW_CONFIDENCE", 0.6),
        destructive_threshold=_float("TRIPWIRE_DESTRUCTIVE_THRESHOLD", 0.8),
        off_task_threshold=_float("TRIPWIRE_OFF_TASK_THRESHOLD", 0.8),
    )
