"""Runtime configuration for ApexTelemetry.

Credentials are read from the environment on the server only. Nothing in this
module is ever serialised to the browser — the frontend talks to this backend,
which acts as the authenticated proxy to the upstream provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


@dataclass(frozen=True)
class ProviderSettings:
    """Settings for the upstream data provider.

    OpenF1's Community tier needs no credential at all. A key is only required
    for the Sponsor tier, which is what unlocks the live window and the
    streaming transports. ``has_live_access`` is therefore the single source of
    truth for whether this deployment may legitimately describe data as live.
    """

    name: str = "openf1"
    base_url: str = "https://api.openf1.org/v1"
    api_key: str | None = None

    # Community tier published limits: 3 req/s, 30 req/min.
    # Sponsor tier: 6 req/s, 60 req/min. We stay one notch under either.
    requests_per_second: float = 2.5
    requests_per_minute: float = 28.0

    timeout_seconds: float = 30.0
    max_retries: int = 4
    backoff_base_seconds: float = 0.75

    @property
    def has_live_access(self) -> bool:
        return bool(self.api_key)

    @property
    def tier(self) -> str:
        return "sponsor" if self.has_live_access else "community"


@dataclass(frozen=True)
class Settings:
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    cache_dir: Path = Path(os.getenv("APEX_CACHE_DIR", ".apex-cache"))

    # Completed sessions never change, so they are cached indefinitely on disk.
    # Anything inside the live window is held only for a few seconds in memory.
    live_cache_seconds: float = 3.0
    recent_cache_seconds: float = 120.0

    # OpenF1 treats a session as live from 30 min before to 30 min after.
    live_window_minutes: int = 30

    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")

    # Optional: grounded AI race-engineer reports. Absent key => the endpoint
    # returns the computed evidence bundle without narrative synthesis.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-5"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    provider = ProviderSettings(
        base_url=os.getenv("OPENF1_BASE_URL", "https://api.openf1.org/v1").rstrip("/"),
        api_key=os.getenv("OPENF1_API_KEY") or None,
        requests_per_second=_env_float("OPENF1_RPS", 5.0 if os.getenv("OPENF1_API_KEY") else 2.5),
        requests_per_minute=_env_float("OPENF1_RPM", 55.0 if os.getenv("OPENF1_API_KEY") else 28.0),
        timeout_seconds=_env_float("OPENF1_TIMEOUT", 30.0),
        max_retries=_env_int("OPENF1_MAX_RETRIES", 4),
    )
    origins = os.getenv("APEX_CORS_ORIGINS")
    return Settings(
        provider=provider,
        cache_dir=Path(os.getenv("APEX_CACHE_DIR", ".apex-cache")),
        cors_origins=tuple(o.strip() for o in origins.split(",")) if origins else Settings.cors_origins,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5"),
    )
