"""Typed response models for upstream provider payloads.

Every model is deliberately permissive about missing fields: OpenF1 returns
partial rows for incomplete sessions, for cars that retired, and for any
channel the timing feed did not emit. A missing value must survive as ``None``
all the way to the UI so it can be rendered as "no data" rather than silently
becoming a zero that looks like a real measurement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

Provenance = Literal["measured", "calculated", "estimated", "unavailable"]
"""How a number came to exist.

``measured``   straight from the provider's timing feed.
``calculated`` deterministic arithmetic over measured values (deltas, sums).
``estimated``  depends on a model with assumptions (degradation, fuel, apex).
``unavailable``the dataset does not support this figure at all.
"""


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


class Meeting(Base):
    meeting_key: int
    meeting_name: str | None = None
    meeting_official_name: str | None = None
    location: str | None = None
    country_name: str | None = None
    country_code: str | None = None
    circuit_key: int | None = None
    circuit_short_name: str | None = None
    date_start: datetime | None = None
    gmt_offset: str | None = None
    year: int | None = None


class Session(Base):
    session_key: int
    meeting_key: int | None = None
    session_name: str | None = None
    session_type: str | None = None
    date_start: datetime | None = None
    date_end: datetime | None = None
    gmt_offset: str | None = None
    location: str | None = None
    country_name: str | None = None
    circuit_short_name: str | None = None
    circuit_key: int | None = None
    year: int | None = None


class Driver(Base):
    driver_number: int
    session_key: int | None = None
    meeting_key: int | None = None
    broadcast_name: str | None = None
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    name_acronym: str | None = None
    team_name: str | None = None
    team_colour: str | None = None
    country_code: str | None = None
    headshot_url: str | None = None

    @field_validator("team_colour")
    @classmethod
    def _normalise_colour(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip().lstrip("#")
        return f"#{v.upper()}" if len(v) in (3, 6) else None

    @property
    def label(self) -> str:
        return self.name_acronym or self.broadcast_name or self.full_name or str(self.driver_number)


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


class Lap(Base):
    driver_number: int
    lap_number: int | None = None
    session_key: int | None = None
    date_start: datetime | None = None
    lap_duration: float | None = None
    duration_sector_1: float | None = None
    duration_sector_2: float | None = None
    duration_sector_3: float | None = None
    segments_sector_1: list[int | None] | None = None
    segments_sector_2: list[int | None] | None = None
    segments_sector_3: list[int | None] | None = None
    i1_speed: float | None = None
    i2_speed: float | None = None
    st_speed: float | None = None
    is_pit_out_lap: bool | None = None

    @property
    def sectors(self) -> list[float | None]:
        return [self.duration_sector_1, self.duration_sector_2, self.duration_sector_3]


class CarSample(Base):
    """One car-telemetry sample. OpenF1 publishes these at roughly 3.7 Hz."""

    date: datetime
    driver_number: int
    speed: float | None = None  # km/h
    throttle: float | None = None  # 0-100 %
    brake: float | None = None  # 0 or 100 (on/off), not pressure
    rpm: float | None = None
    n_gear: int | None = None
    drs: int | None = None

    @property
    def drs_active(self) -> bool | None:
        """OpenF1 mirrors the F1 feed's DRS codes.

        10, 12 and 14 mean the flap is open. 8 means the car is within a second
        at the detection point but has not yet opened it. 0 and 1 mean off.
        Anything we do not recognise returns ``None`` rather than a guess.
        """
        if self.drs is None:
            return None
        if self.drs in (10, 12, 14):
            return True
        if self.drs in (0, 1, 8):
            return False
        return None


class LocationSample(Base):
    """Car position on track, roughly 3.7 Hz, in a circuit-local frame."""

    date: datetime
    driver_number: int
    x: float | None = None
    y: float | None = None
    z: float | None = None


class Stint(Base):
    driver_number: int
    stint_number: int | None = None
    compound: str | None = None
    lap_start: int | None = None
    lap_end: int | None = None
    tyre_age_at_start: int | None = None


class PitStop(Base):
    driver_number: int
    date: datetime | None = None
    lap_number: int | None = None
    pit_duration: float | None = None


class Interval(Base):
    date: datetime
    driver_number: int
    gap_to_leader: float | str | None = None
    interval: float | str | None = None


class PositionSample(Base):
    date: datetime
    driver_number: int
    position: int | None = None


class RaceControlMessage(Base):
    date: datetime | None = None
    category: str | None = None
    flag: str | None = None
    scope: str | None = None
    sector: int | None = None
    lap_number: int | None = None
    driver_number: int | None = None
    message: str | None = None


class Weather(Base):
    date: datetime | None = None
    air_temperature: float | None = None
    track_temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    rainfall: float | int | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None


class TeamRadio(Base):
    date: datetime | None = None
    driver_number: int | None = None
    recording_url: str | None = None


# --------------------------------------------------------------------------
# Session health / freshness
# --------------------------------------------------------------------------


class FeedStatus(Base):
    """What the UI needs in order to tell the truth about the data on screen."""

    provider: str
    tier: str
    mode: Literal["live", "historical", "archive"]
    live_access: bool = Field(
        description="True only when this deployment holds a credential permitting live data."
    )
    session_key: int | None = None
    last_updated: datetime | None = None
    age_seconds: float | None = None
    stale: bool = False
    stale_threshold_seconds: float = 15.0
    degraded_reason: str | None = None


class Measurement(Base):
    """A number plus the story of where it came from."""

    value: float | None = None
    unit: str | None = None
    provenance: Provenance = "measured"
    note: str | None = None
    sample_size: int | None = None
