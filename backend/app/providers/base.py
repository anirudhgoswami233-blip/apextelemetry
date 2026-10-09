"""Provider interface.

Everything above this line in the stack (analytics, routers, UI) is written
against ``TelemetryProvider`` and ``ProviderCapabilities`` only. Adding a new
licensed source means writing one adapter, not touching the frontend.

The capability flags exist so the UI can disable a workspace honestly rather
than rendering a degraded version of it. A provider that cannot supply
sample-level car telemetry must say so, and the telemetry laboratory and
corner analysis pages will refuse to open against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

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


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can actually do. No optimism allowed here."""

    name: str
    display_name: str

    # Data shape
    raw_telemetry: bool = False
    """Sample-level speed/throttle/brake/rpm/gear/drs. Required by the
    telemetry laboratory (spec §5) and corner analysis (§6)."""

    car_location: bool = False
    """x/y/z position samples. Required to derive corner geometry (§6)."""

    live: bool = False
    """May legitimately serve data inside the live window."""

    streaming: bool = False
    """Push transport available (WebSocket/MQTT) rather than polling."""

    race_control: bool = False
    weather: bool = False
    stints: bool = False
    pit_stops: bool = False
    intervals: bool = False
    team_radio: bool = False

    first_season: int | None = None
    last_season: int | None = None

    requires_key: bool = False
    has_key: bool = False

    attribution: str = ""
    licence_note: str = ""

    def unsupported_workspaces(self) -> list[str]:
        missing: list[str] = []
        if not self.raw_telemetry:
            missing += ["telemetry-lab", "lap-delta"]
        if not (self.raw_telemetry and self.car_location):
            missing.append("corner-analysis")
        if not self.stints:
            missing.append("strategy")
        if not self.live:
            missing.append("live-timing")
        return sorted(set(missing))


class ProviderError(RuntimeError):
    """Upstream failed in a way the user needs to be told about."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class CapabilityError(ProviderError):
    """The request is valid but this provider structurally cannot answer it."""


@runtime_checkable
class TelemetryProvider(Protocol):
    capabilities: ProviderCapabilities

    async def meetings(self, year: int) -> list[Meeting]: ...

    async def sessions(self, *, year: int | None = None, meeting_key: int | None = None) -> list[Session]: ...

    async def session(self, session_key: int) -> Session | None: ...

    async def drivers(self, session_key: int) -> list[Driver]: ...

    async def laps(self, session_key: int, driver_number: int | None = None) -> list[Lap]: ...

    async def car_data(
        self,
        session_key: int,
        driver_number: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CarSample]: ...

    async def location(
        self,
        session_key: int,
        driver_number: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[LocationSample]: ...

    async def stints(self, session_key: int) -> list[Stint]: ...

    async def pit_stops(self, session_key: int) -> list[PitStop]: ...

    async def intervals(self, session_key: int) -> list[Interval]: ...

    async def positions(self, session_key: int) -> list[PositionSample]: ...

    async def race_control(self, session_key: int) -> list[RaceControlMessage]: ...

    async def weather(self, session_key: int) -> list[Weather]: ...

    async def team_radio(self, session_key: int) -> list[TeamRadio]: ...

    async def aclose(self) -> None: ...
