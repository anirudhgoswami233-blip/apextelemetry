"""Lap-time delta on a distance axis, and where the time actually went.

The delta trace answers one question: at this point on the circuit, how much
time separates the two laps? It is built by interpolating each lap's elapsed
time onto a shared distance grid and subtracting:

    delta(d) = t_b(d) - t_a(d)

A positive value means lap B is behind lap A at that point. The *slope* of the
delta is what matters for analysis — a rising delta is time being lost right
there; a flat delta means the two laps are equally quick through that stretch
even if a gap already exists.

Two cautions that the output records rather than hides:

* The delta inherits the integration error of both distance axes. A systematic
  length discrepancy shows up as a spurious drift. The length discrepancy is
  always reported alongside.
* Sector deltas computed from the *timing feed* are measured values and are far
  more trustworthy than anything derived from the integrated axis. Where both
  exist, the output carries both so they can be cross-checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import Lap
from .alignment import AlignedPair, LapTrace, align


@dataclass
class DeltaRegion:
    """A contiguous stretch where one lap was consistently quicker."""

    start_m: float
    end_m: float
    delta_change_s: float  # positive => lap B lost time across this stretch
    peak_speed_difference_kph: float | None = None

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m

    @property
    def favours(self) -> str:
        return "a" if self.delta_change_s > 0 else "b"


@dataclass
class SectorDelta:
    sector: int
    a_seconds: float | None
    b_seconds: float | None
    delta_seconds: float | None
    provenance: str = "measured"


@dataclass
class LapDelta:
    distance: np.ndarray
    delta: np.ndarray
    speed_difference: np.ndarray

    driver_a: int
    driver_b: int
    lap_a: int | None
    lap_b: int | None

    final_delta_s: float
    max_delta_s: float
    min_delta_s: float

    regions: list[DeltaRegion] = field(default_factory=list)
    sectors: list[SectorDelta] = field(default_factory=list)

    alignment_mode: str = "truncate"
    length_a_m: float = 0.0
    length_b_m: float = 0.0
    spatial_resolution_m: float = float("nan")
    notes: list[str] = field(default_factory=list)

    @property
    def biggest_loss(self) -> DeltaRegion | None:
        """Where lap B lost the most time to lap A."""
        gains = [r for r in self.regions if r.delta_change_s > 0]
        return max(gains, key=lambda r: r.delta_change_s) if gains else None

    @property
    def biggest_gain(self) -> DeltaRegion | None:
        losses = [r for r in self.regions if r.delta_change_s < 0]
        return min(losses, key=lambda r: r.delta_change_s) if losses else None


def compute_delta(
    a: LapTrace,
    b: LapTrace,
    *,
    mode: str = "truncate",
    step_m: float = 5.0,
    min_region_m: float = 25.0,
    min_region_s: float = 0.02,
    lap_a: Lap | None = None,
    lap_b: Lap | None = None,
) -> LapDelta:
    pair: AlignedPair = align(a, b, mode=mode, step_m=step_m)  # type: ignore[arg-type]

    t_a = pair.a.time_at(pair.grid)
    t_b = pair.b.time_at(pair.grid)
    delta = t_b - t_a
    speed_difference = pair.b.speed - pair.a.speed

    regions = _segment(pair.grid, delta, speed_difference, min_region_m, min_region_s)

    notes = list(pair.notes)
    resolution = max(a.spatial_resolution(), b.spatial_resolution())
    if np.isfinite(resolution):
        notes.append(
            f"event localisation on this comparison is no better than ~{resolution:.0f} m, "
            f"set by the provider's telemetry sample spacing"
        )
    notes.extend(a.warnings)
    notes.extend(b.warnings)

    return LapDelta(
        distance=pair.grid,
        delta=delta,
        speed_difference=speed_difference,
        driver_a=a.driver_number,
        driver_b=b.driver_number,
        lap_a=a.lap_number,
        lap_b=b.lap_number,
        final_delta_s=float(delta[-1]) if delta.size else 0.0,
        max_delta_s=float(np.nanmax(delta)) if delta.size else 0.0,
        min_delta_s=float(np.nanmin(delta)) if delta.size else 0.0,
        regions=regions,
        sectors=sector_delta(lap_a, lap_b),
        alignment_mode=pair.mode,
        length_a_m=pair.length_a,
        length_b_m=pair.length_b,
        spatial_resolution_m=resolution,
        notes=notes,
    )


def _segment(
    distance: np.ndarray,
    delta: np.ndarray,
    speed_difference: np.ndarray,
    min_region_m: float,
    min_region_s: float,
) -> list[DeltaRegion]:
    """Split the lap into runs where the delta moves consistently one way.

    Noise in the integrated axis makes the raw derivative flip sign constantly,
    so the sign is taken from a lightly smoothed derivative and runs shorter
    than ``min_region_m`` or worth less than ``min_region_s`` are discarded.
    """
    if delta.size < 3:
        return []

    derivative = np.gradient(delta, distance)
    window = max(3, int(round(50.0 / max(distance[1] - distance[0], 1e-6))) | 1)
    kernel = np.ones(window) / window
    smoothed = np.convolve(derivative, kernel, mode="same")

    sign = np.sign(smoothed)
    sign[sign == 0] = 1.0
    boundaries = np.flatnonzero(np.diff(sign)) + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [delta.size - 1]])

    regions: list[DeltaRegion] = []
    for s, e in zip(starts, ends):
        if e <= s:
            continue
        span = float(distance[e] - distance[s])
        change = float(delta[e] - delta[s])
        if span < min_region_m or abs(change) < min_region_s:
            continue
        window_speed = speed_difference[s : e + 1]
        peak = (
            float(window_speed[np.nanargmax(np.abs(window_speed))])
            if window_speed.size and np.isfinite(window_speed).any()
            else None
        )
        regions.append(
            DeltaRegion(
                start_m=float(distance[s]),
                end_m=float(distance[e]),
                delta_change_s=change,
                peak_speed_difference_kph=peak,
            )
        )
    return regions


def sector_delta(lap_a: Lap | None, lap_b: Lap | None) -> list[SectorDelta]:
    """Sector comparison straight from the timing feed.

    These are measured values. Where they disagree with the integrated delta,
    believe these.
    """
    if lap_a is None or lap_b is None:
        return []
    out: list[SectorDelta] = []
    for index, (sa, sb) in enumerate(zip(lap_a.sectors, lap_b.sectors), start=1):
        out.append(
            SectorDelta(
                sector=index,
                a_seconds=sa,
                b_seconds=sb,
                delta_seconds=(sb - sa) if (sa is not None and sb is not None) else None,
                provenance="measured" if (sa is not None and sb is not None) else "unavailable",
            )
        )
    return out


# ----------------------------------------------------------------------
# Lap selection
# ----------------------------------------------------------------------


@dataclass
class LapQuality:
    lap: Lap
    is_valid: bool
    flags: list[str] = field(default_factory=list)

    @property
    def lap_number(self) -> int | None:
        return self.lap.lap_number


def classify_laps(
    laps: list[Lap],
    *,
    pit_laps: set[int] | None = None,
    yellow_flag_laps: set[int] | None = None,
    outlier_factor: float = 1.07,
) -> list[LapQuality]:
    """Flag laps that should not be used for a pace comparison.

    Nothing here is deleted. A lap is flagged with the reason it is suspect so
    the UI can grey it out while still letting an engineer select it on
    purpose. ``outlier_factor`` is applied against the driver's own session
    best, the conventional 107% style test.
    """
    pit_laps = pit_laps or set()
    yellow_flag_laps = yellow_flag_laps or set()

    timed = [l.lap_duration for l in laps if l.lap_duration is not None]
    reference = min(timed) if timed else None

    out: list[LapQuality] = []
    for lap in laps:
        flags: list[str] = []
        if lap.lap_duration is None:
            flags.append("no lap time recorded")
        if lap.is_pit_out_lap:
            flags.append("pit-out lap")
        if lap.lap_number is not None and lap.lap_number in pit_laps:
            flags.append("in-lap (pit entry on this lap)")
        if lap.lap_number is not None and lap.lap_number in yellow_flag_laps:
            flags.append("yellow flag shown during this lap")
        if any(s is None for s in lap.sectors):
            flags.append("incomplete sector times")
        if (
            reference is not None
            and lap.lap_duration is not None
            and lap.lap_duration > reference * outlier_factor
        ):
            flags.append(
                f"{lap.lap_duration / reference:.1%} of the driver's session best — "
                "likely traffic, a lift, or a compromised lap"
            )
        out.append(LapQuality(lap=lap, is_valid=not flags, flags=flags))
    return out


def representative_laps(qualities: list[LapQuality], limit: int = 5) -> list[Lap]:
    """The quickest clean laps, for a fair pace comparison."""
    clean = [q.lap for q in qualities if q.is_valid and q.lap.lap_duration is not None]
    clean.sort(key=lambda l: l.lap_duration or float("inf"))
    return clean[:limit]
