"""Provider registry.

One place decides which adapter serves a request. Routers never import a
concrete provider.
"""

from __future__ import annotations

import os
from functools import lru_cache

from ..config import Settings, get_settings
from .base import (
    CapabilityError,
    ProviderCapabilities,
    ProviderError,
    TelemetryProvider,
)
from .openf1 import OpenF1Provider
from .t1api import T1ApiProvider

__all__ = [
    "CapabilityError",
    "ProviderCapabilities",
    "ProviderError",
    "TelemetryProvider",
    "OpenF1Provider",
    "T1ApiProvider",
    "get_provider",
    "list_providers",
    "shutdown_providers",
]

_instances: dict[str, object] = {}


def get_provider(name: str | None = None, settings: Settings | None = None):
    settings = settings or get_settings()
    name = (name or settings.provider.name).lower()

    if name in _instances:
        return _instances[name]

    if name == "openf1":
        provider = OpenF1Provider(settings)
    elif name == "t1api":
        provider = T1ApiProvider(api_key=os.getenv("T1API_KEY"))
    else:
        raise ProviderError(f"Unknown provider '{name}'. Registered: openf1, t1api.")

    _instances[name] = provider
    return provider


def list_providers(settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    out = []
    for name in ("openf1", "t1api"):
        caps = get_provider(name, settings).capabilities
        out.append(
            {
                **caps.__dict__,
                "enabled": name == "openf1",
                "unsupported_workspaces": caps.unsupported_workspaces(),
            }
        )
    return out


async def shutdown_providers() -> None:
    for provider in list(_instances.values()):
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    _instances.clear()
