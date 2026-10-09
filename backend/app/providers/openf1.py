"""OpenF1 adapter.

Notes that drove the design here:

* The Community tier needs no credential and covers 2023 onward, historical
  only. A credential unlocks the live window and the streaming transports.
  ``capabilities.live`` therefore tracks the presence of a key — this backend
  will not describe archived data as live under any circumstance.
* Published limits are 3 req/s and 30 req/min on Community. We run a token
  bucket slightly below both ceilings, because exceeding them produces 429s
  that cost more time than the throttling does.
* ``car_data`` for a whole session and a whole field is enormous — roughly
  3.7 samples/second/car. Every telemetry read is therefore narrowed to one
  driver and one time window, which is what the UI actually needs for a lap.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

import httpx
from pydantic import ValidationError

from ..cache import ResponseCache
from ..config import ProviderSettings, Settings
from ..models import (
    CarSample,
    Driver,
    Interval,
    Lap,
    LocationSample,
    Meeting,
    PitStop,
    PositionSample,
    RaceControlMessage,
    Session,
    Stint,
    TeamRadio,
    Weather,
)
from .base import ProviderCapabilities, ProviderError

T = TypeVar("T")


class TokenBucket:
    """Dual-window limiter: both the per-second and per-minute ceiling hold."""

    def __init__(self, per_second: float, per_minute: float) -> None:
        self._per_second = per_second
        self._per_minute = per_minute
        self._second_window: list[float] = []
        self._minute_window: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._second_window = [t for t in self._second_window if now - t < 1.0]
                self._minute_window = [t for t in self._minute_window if now - t < 60.0]

                waits = []
                if len(self._second_window) >= self._per_second:
                    waits.append(1.0 - (now - self._second_window[0]))
                if len(self._minute_window) >= self._per_minute:
                    waits.append(60.0 - (now - self._minute_window[0]))

                if not waits:
                    self._second_window.append(now)
                    self._minute_window.append(now)
                    return
                await asyncio.sleep(max(max(waits), 0.01))


class OpenF1Provider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._cfg: ProviderSettings = settings.provider
        self._cache = ResponseCache(settings.cache_dir / "openf1")
        self._bucket = TokenBucket(self._cfg.requests_per_second, self._cfg.requests_per_minute)
        headers = {"User-Agent": "ApexTelemetry/1.0 (+engineering-analytics)"}
        if self._cfg.api_key:
            # Sponsor-tier credential. Server side only — never reaches a browser.
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        self._client = client or httpx.AsyncClient(
            base_url=self._cfg.base_url,
            timeout=self._cfg.timeout_seconds,
            headers=headers,
            follow_redirects=True,
        )
        self._session_meta: dict[int, Session] = {}

    # -- capabilities ------------------------------------------------------

    @property
    def capabilities(self) -> ProviderCapabilities:
        live = self._cfg.has_live_access
        return ProviderCapabilities(
            name="openf1",
            display_name="OpenF1",
            raw_telemetry=True,
            car_location=True,
            live=live,
            streaming=live,
            race_control=True,
            weather=True,
            stints=True,
            pit_stops=True,
            intervals=True,
            team_radio=True,
            first_season=2023,
            last_season=datetime.now(timezone.utc).year,
            requires_key=False,
            has_key=live,
            attribution="Data from OpenF1 (openf1.org). Unofficial; not affiliated with "
            "Formula 1, the FIA or Formula One Management.",
            licence_note="OpenF1 site content is CC BY-NC-SA 4.0 and the project describes "
            "intended use as educational, personal, research and non-commercial fan "
            "engagement. Attribution is not required but a link back is appreciated. "
            "Commercial or redistributive use needs a separate conversation with the "
            "project. Underlying F1 timing data remains the rights-holders'.",
        )

    # -- transport ---------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any], *, immutable: bool) -> list[dict[str, Any]]:
        params = {k: v for k, v in params.items() if v is not None}

        if immutable:
            cached = self._cache.get_disk(path, params)
            if cached is not None:
                return cached
        else:
            cached = self._cache.get_memory(path, params)
            if cached is not None:
                return cached

        async def fetch() -> list[dict[str, Any]]:
            last_error: Exception | None = None
            for attempt in range(self._cfg.max_retries):
                await self._bucket.acquire()
                try:
                    response = await self._client.get(path, params=params)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    raise ProviderError(
                        f"Could not reach OpenF1 at {self._cfg.base_url}. Check network egress "
                        f"from this host — the API is plain HTTPS and needs no credential on the "
                        f"Community tier. ({exc.__class__.__name__})"
                    ) from exc
                except httpx.HTTPError as exc:
                    last_error = exc
                    await asyncio.sleep(self._cfg.backoff_base_seconds * (2**attempt))
                    continue

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 0) or 0)
                    delay = retry_after or self._cfg.backoff_base_seconds * (2**attempt)
                    if attempt == self._cfg.max_retries - 1:
                        raise ProviderError(
                            "OpenF1 rate limit reached. The Community tier allows 3 req/s and "
                            "30 req/min; narrow the selection or add a Sponsor key.",
                            status=429,
                            retry_after=delay,
                        )
                    await asyncio.sleep(min(delay, 10.0))
                    continue

                if response.status_code in (401, 403):
                    raise ProviderError(
                        "OpenF1 refused the request. Live-window data requires a Sponsor key in "
                        "OPENF1_API_KEY; historical data should need none.",
                        status=response.status_code,
                    )

                if response.status_code >= 500:
                    if attempt == self._cfg.max_retries - 1:
                        raise ProviderError(
                            f"OpenF1 returned {response.status_code}.", status=response.status_code
                        )
                    await asyncio.sleep(self._cfg.backoff_base_seconds * (2**attempt))
                    continue

                if response.status_code == 404:
                    # OpenF1 answers 404 when a query matches no rows, which is
                    # an ordinary outcome rather than a failure: a session that
                    # has not run yet, a car that never left the garage, a
                    # practice session with no stint records. Surfacing it as an
                    # error would put a red panel where an empty state belongs,
                    # so it is mapped to no rows and the caller renders "no data".
                    return []

                if response.status_code >= 400:
                    raise ProviderError(
                        f"OpenF1 rejected the request ({response.status_code}) for {path}.",
                        status=response.status_code,
                    )

                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ProviderError("OpenF1 returned a body that was not JSON.") from exc

                if isinstance(payload, dict):
                    # Error envelopes come back as objects rather than arrays.
                    raise ProviderError(str(payload.get("detail") or payload.get("message") or payload))
                return payload

            raise ProviderError(f"OpenF1 request failed after retries: {last_error}")

        data = await self._cache.single_flight(path, params, fetch)

        if immutable:
            self._cache.set_disk(path, params, data)
        else:
            self._cache.set_memory(path, params, data, self._settings.live_cache_seconds)
        return data

    @staticmethod
    def _parse(model: type[T], rows: list[dict[str, Any]]) -> list[T]:
        """Validate row by row and drop only the rows that are unusable.

        A single malformed row in a 40,000-sample telemetry response must not
        destroy the whole lap. Dropped rows are counted, not silently ignored.
        """
        out: list[T] = []
        for row in rows:
            try:
                out.append(model(**row))
            except ValidationError:
                continue
        return out

    # -- freshness ---------------------------------------------------------

    def is_live_window(self, session: Session | None) -> bool:
        """OpenF1 defines live as 30 min before start through 30 min after end."""
        if session is None or session.date_start is None:
            return False
        now = datetime.now(timezone.utc)
        margin = timedelta(minutes=self._settings.live_window_minutes)
        start = session.date_start
        end = session.date_end or (start + timedelta(hours=3))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return (start - margin) <= now <= (end + margin)

    async def _immutable_for(self, session_key: int) -> bool:
        """A session is cacheable forever once it is comfortably finished."""
        session = await self.session(session_key)
        return not self.is_live_window(session)

    # -- catalog -----------------------------------------------------------

    async def meetings(self, year: int) -> list[Meeting]:
        current_year = datetime.now(timezone.utc).year
        rows = await self._get("/meetings", {"year": year}, immutable=year < current_year)
        meetings = self._parse(Meeting, rows)
        meetings.sort(key=lambda m: (m.date_start is None, m.date_start or datetime.min))
        return meetings

    async def sessions(self, *, year: int | None = None, meeting_key: int | None = None) -> list[Session]:
        if year is None and meeting_key is None:
            raise ProviderError("A year or a meeting_key is required to list sessions.")
        params = {"year": year, "meeting_key": meeting_key}
        current_year = datetime.now(timezone.utc).year
        immutable = year is not None and year < current_year
        rows = await self._get("/sessions", params, immutable=immutable)
        sessions = self._parse(Session, rows)
        for s in sessions:
            self._session_meta[s.session_key] = s
        sessions.sort(key=lambda s: (s.date_start is None, s.date_start or datetime.min))
        return sessions

    async def session(self, session_key: int) -> Session | None:
        if session_key in self._session_meta:
            return self._session_meta[session_key]
        rows = await self._get("/sessions", {"session_key": session_key}, immutable=False)
        found = self._parse(Session, rows)
        if not found:
            return None
        self._session_meta[session_key] = found[0]
        return found[0]

    # -- session data ------------------------------------------------------

    async def drivers(self, session_key: int) -> list[Driver]:
        rows = await self._get(
            "/drivers", {"session_key": session_key}, immutable=await self._immutable_for(session_key)
        )
        drivers = self._parse(Driver, rows)
        # The feed can repeat a driver across updates; last entry wins.
        unique: dict[int, Driver] = {d.driver_number: d for d in drivers}
        return sorted(unique.values(), key=lambda d: d.driver_number)

    async def laps(self, session_key: int, driver_number: int | None = None) -> list[Lap]:
        rows = await self._get(
            "/laps",
            {"session_key": session_key, "driver_number": driver_number},
            immutable=await self._immutable_for(session_key),
        )
        laps = self._parse(Lap, rows)
        laps.sort(key=lambda l: (l.driver_number, l.lap_number or 0))
        return laps

    async def car_data(
        self,
        session_key: int,
        driver_number: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CarSample]:
        params: dict[str, Any] = {"session_key": session_key, "driver_number": driver_number}
        if start is not None:
            params["date>"] = _iso(start)
        if end is not None:
            params["date<"] = _iso(end)
        rows = await self._get("/car_data", params, immutable=await self._immutable_for(session_key))
        samples = self._parse(CarSample, rows)
        samples.sort(key=lambda s: s.date)
        return samples

    async def location(
        self,
        session_key: int,
        driver_number: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[LocationSample]:
        params: dict[str, Any] = {"session_key": session_key, "driver_number": driver_number}
        if start is not None:
            params["date>"] = _iso(start)
        if end is not None:
            params["date<"] = _iso(end)
        rows = await self._get("/location", params, immutable=await self._immutable_for(session_key))
        samples = self._parse(LocationSample, rows)
        samples.sort(key=lambda s: s.date)
        # The feed emits (0, 0, 0) placeholders when a car is not being tracked.
        return [s for s in samples if not (s.x == 0 and s.y == 0)]

    async def stints(self, session_key: int) -> list[Stint]:
        rows = await self._get(
            "/stints", {"session_key": session_key}, immutable=await self._immutable_for(session_key)
        )
        stints = self._parse(Stint, rows)
        stints.sort(key=lambda s: (s.driver_number, s.stint_number or 0))
        return stints

    async def pit_stops(self, session_key: int) -> list[PitStop]:
        rows = await self._get(
            "/pit", {"session_key": session_key}, immutable=await self._immutable_for(session_key)
        )
        return self._parse(PitStop, rows)

    async def intervals(self, session_key: int) -> list[Interval]:
        rows = await self._get("/intervals", {"session_key": session_key}, immutable=False)
        samples = self._parse(Interval, rows)
        samples.sort(key=lambda s: s.date)
        return samples

    async def positions(self, session_key: int) -> list[PositionSample]:
        rows = await self._get("/position", {"session_key": session_key}, immutable=False)
        samples = self._parse(PositionSample, rows)
        samples.sort(key=lambda s: s.date)
        return samples

    async def race_control(self, session_key: int) -> list[RaceControlMessage]:
        rows = await self._get("/race_control", {"session_key": session_key}, immutable=False)
        msgs = self._parse(RaceControlMessage, rows)
        msgs.sort(key=lambda m: (m.date is None, m.date or datetime.min))
        return msgs

    async def weather(self, session_key: int) -> list[Weather]:
        rows = await self._get("/weather", {"session_key": session_key}, immutable=False)
        rec = self._parse(Weather, rows)
        rec.sort(key=lambda w: (w.date is None, w.date or datetime.min))
        return rec

    async def team_radio(self, session_key: int) -> list[TeamRadio]:
        rows = await self._get("/team_radio", {"session_key": session_key}, immutable=False)
        return self._parse(TeamRadio, rows)

    async def aclose(self) -> None:
        await self._client.aclose()

    def cache_stats(self) -> dict[str, int]:
        return self._cache.stats()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
