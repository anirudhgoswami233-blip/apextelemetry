"""Two-tier cache: short-lived memory for live data, durable disk for archives.

A completed Formula 1 session is immutable. Once we have downloaded a race's
telemetry there is no reason ever to download it again, and re-downloading it
would burn the provider's rate limit for nothing. So:

* Requests tagged ``immutable`` land on disk and are served from there forever.
* Requests tagged ``volatile`` sit in memory for a few seconds, which is enough
  to collapse the fan-out when eight UI panels all ask for the same poll tick.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _key(url: str, params: dict[str, Any]) -> str:
    canonical = url + "?" + json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:40]


@dataclass
class _Entry:
    value: Any
    expires_at: float


class ResponseCache:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._memory: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Future] = {}

    # -- memory tier -------------------------------------------------------

    def get_memory(self, url: str, params: dict[str, Any]) -> Any | None:
        entry = self._memory.get(_key(url, params))
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            return None
        return entry.value

    def set_memory(self, url: str, params: dict[str, Any], value: Any, ttl: float) -> None:
        self._memory[_key(url, params)] = _Entry(value, time.monotonic() + ttl)
        if len(self._memory) > 2048:
            now = time.monotonic()
            for k in [k for k, v in self._memory.items() if v.expires_at < now]:
                self._memory.pop(k, None)

    # -- disk tier ---------------------------------------------------------

    def _path(self, url: str, params: dict[str, Any]) -> Path:
        k = _key(url, params)
        return self._dir / k[:2] / f"{k}.json.gz"

    def get_disk(self, url: str, params: dict[str, Any]) -> Any | None:
        path = self._path(url, params)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            # A truncated cache file is not an error worth surfacing; drop it
            # and let the caller re-fetch.
            path.unlink(missing_ok=True)
            return None

    def set_disk(self, url: str, params: dict[str, Any], value: Any) -> None:
        path = self._path(url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(value, fh, default=str)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)

    # -- request coalescing ------------------------------------------------

    async def single_flight(self, url: str, params: dict[str, Any], factory):
        """Ensure only one in-flight upstream call exists per distinct request."""
        k = _key(url, params)
        async with self._lock:
            existing = self._inflight.get(k)
            if existing is not None:
                fut = existing
            else:
                fut = asyncio.get_running_loop().create_future()
                self._inflight[k] = fut
                existing = None
        if existing is not None:
            return await existing
        try:
            result = await factory()
        except BaseException as exc:  # noqa: BLE001 - propagated to every waiter
            async with self._lock:
                self._inflight.pop(k, None)
            if not fut.done():
                fut.set_exception(exc)
            raise
        async with self._lock:
            self._inflight.pop(k, None)
        if not fut.done():
            fut.set_result(result)
        return result

    def stats(self) -> dict[str, int]:
        files = list(self._dir.rglob("*.json.gz")) if self._dir.exists() else []
        return {
            "memory_entries": len(self._memory),
            "disk_entries": len(files),
            "disk_bytes": sum(f.stat().st_size for f in files),
        }
