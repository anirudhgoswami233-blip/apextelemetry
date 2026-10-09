"""Turning irregular telemetry samples into a comparable distance axis.

This module is the foundation everything else stands on, so it is worth being
explicit about what is true and what is assumed.

**The provider gives us time, not distance.** OpenF1 publishes car telemetry at
roughly 3.7 Hz with a timestamp and a speed in km/h. There is no distance
channel. We therefore integrate speed over time to obtain distance travelled:

    d(0) = 0
    d(i) = d(i-1) + ((v(i-1) + v(i)) / 2) * (t(i) - t(i-1))

That is a trapezoidal rule, which is exact for linear speed change between
samples and second-order accurate otherwise. It is the same construction the
public F1 analysis tooling uses, and it is a *calculated* quantity, never a
measured one.

**What this costs us.** At 3.7 Hz a car at 300 km/h covers about 22 m between
consecutive samples. No event derived from this data — a braking point, an
apex, a throttle application — can be located more precisely than roughly half
that gap. Every such result in this codebase carries its own spatial resolution
so the UI can show it rather than implying millimetre precision.

**Comparing two laps.** Two laps of the same circuit will not integrate to
exactly the same length: different racing lines genuinely differ, and the
integration accumulates its own drift. Two alignment modes are offered and the
choice is always recorded in the output:

``truncate``   compare over the distance both laps share. Honest, but discards
               the final few metres of the longer trace.
``normalize``  scale each lap's distance axis onto a shared reference length.
               Appropriate when the discrepancy is measurement drift spread
               evenly round the lap; wrong if one driver took a genuinely
               longer line through one specific corner. Use with care.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Sequence

import numpy as np

from ..models import CarSample, Lap, LocationSample

AlignmentMode = Literal["truncate", "normalize"]

# Channels interpolated continuously vs. held as steps. Gear, DRS and the
# on/off brake flag are categorical: interpolating them would invent a gear 4.3
# or a half-open DRS flap that never existed.
CONTINUOUS_CHANNELS = ("speed", "throttle", "rpm")
STEP_CHANNELS = ("brake", "n_gear", "drs")


@dataclass
class LapTrace:
    """One lap of telemetry on a distance axis."""

    driver_number: int
    lap_number: int | None
    session_key: int | None

    time: np.ndarray            # seconds elapsed from the first sample of the lap
    distance: np.ndarray        # metres from the first sample, integrated from speed
    speed: np.ndarray           # km/h
    throttle: np.ndarray        # 0-100 %
    brake: np.ndarray           # 0 or 100; OpenF1 publishes a flag, not pressure
    rpm: np.ndarray
    n_gear: np.ndarray
    drs: np.ndarray

    x: np.ndarray | None = None
    y: np.ndarray | None = None

    lap_duration: float | None = None
    sample_count: int = 0
    median_interval_s: float | None = None
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------

    @property
    def length(self) -> float:
        return float(self.distance[-1]) if self.distance.size else 0.0

    @property
    def sample_rate_hz(self) -> float | None:
        if not self.median_interval_s:
            return None
        return round(1.0 / self.median_interval_s, 2)

    def spatial_resolution(self, at_distance: float | None = None) -> float:
        """Worst-case metres between samples, optionally local to one point.

        This is the honest precision floor for any event located on this trace.
        """
        if self.distance.size < 2:
            return float("nan")
        gaps = np.diff(self.distance)
        if at_distance is None:
            return float(np.nanmax(gaps))
        idx = int(np.clip(np.searchsorted(self.distance, at_distance) - 1, 0, gaps.size - 1))
        return float(gaps[idx])

    def time_at(self, distance: float | np.ndarray) -> np.ndarray:
        """Elapsed lap time at a given distance, by linear interpolation."""
        d, t = _monotonic(self.distance, self.time)
        return np.interp(distance, d, t)

    def channel(self, name: str) -> np.ndarray:
        return getattr(self, name)

    def resample(self, grid: np.ndarray) -> "LapTrace":
        """Project this lap onto an arbitrary distance grid."""
        order = np.argsort(self.distance, kind="stable")
        d = np.maximum.accumulate(self.distance[order])
        keep = _dedupe_mask(d)
        d = d[keep]
        if d.size < 2:
            raise ValueError("Lap has fewer than two distinct distance samples.")

        def cont(values: np.ndarray) -> np.ndarray:
            return np.interp(grid, d, values[order][keep])

        def step(values: np.ndarray) -> np.ndarray:
            v = values[order][keep]
            idx = np.clip(np.searchsorted(d, grid, side="right") - 1, 0, v.size - 1)
            return v[idx]

        return LapTrace(
            driver_number=self.driver_number,
            lap_number=self.lap_number,
            session_key=self.session_key,
            time=cont(self.time),
            distance=grid.copy(),
            speed=cont(self.speed),
            throttle=cont(self.throttle),
            brake=step(self.brake),
            rpm=cont(self.rpm),
            n_gear=step(self.n_gear),
            drs=step(self.drs),
            x=cont(self.x) if self.x is not None else None,
            y=cont(self.y) if self.y is not None else None,
            lap_duration=self.lap_duration,
            sample_count=self.sample_count,
            median_interval_s=self.median_interval_s,
            warnings=list(self.warnings),
        )


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _dedupe_mask(sorted_values: np.ndarray) -> np.ndarray:
    """Keep the first of each run of identical values."""
    if sorted_values.size == 0:
        return np.zeros(0, dtype=bool)
    mask = np.ones(sorted_values.size, dtype=bool)
    mask[1:] = np.diff(sorted_values) > 0
    return mask


def _monotonic(distance: np.ndarray, time: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return a strictly increasing distance axis and its matching times.

    A stationary or crawling car produces repeated distances. ``np.interp``
    requires strict monotonicity, so duplicates are collapsed to their first
    occurrence rather than nudged by an epsilon, which would fabricate motion.
    """
    order = np.argsort(distance, kind="stable")
    d = distance[order]
    t = time[order]
    d = np.maximum.accumulate(d)
    mask = _dedupe_mask(d)
    if mask.sum() < 2:
        return d[:2], t[:2]
    return d[mask], t[mask]


def integrate_distance(time_s: np.ndarray, speed_kph: np.ndarray) -> np.ndarray:
    """Cumulative trapezoidal integration of speed to obtain distance in metres."""
    if time_s.size == 0:
        return np.zeros(0)
    if time_s.size == 1:
        return np.zeros(1)
    v = np.nan_to_num(speed_kph, nan=0.0) / 3.6  # km/h -> m/s
    dt = np.diff(time_s)
    segment = 0.5 * (v[1:] + v[:-1]) * dt
    return np.concatenate([[0.0], np.cumsum(segment)])


def build_lap_trace(
    samples: Sequence[CarSample],
    *,
    lap: Lap | None = None,
    locations: Sequence[LocationSample] | None = None,
    session_key: int | None = None,
) -> LapTrace | None:
    """Assemble a distance-indexed trace from raw provider samples.

    ``locations`` are matched to car-telemetry timestamps by interpolation,
    because the two feeds are published independently and their sample instants
    do not coincide. They are never paired positionally.
    """
    samples = [s for s in samples if s.speed is not None]
    if len(samples) < 2:
        return None

    samples = sorted(samples, key=lambda s: s.date)
    t0 = _as_utc(samples[0].date)
    time_s = np.array([(_as_utc(s.date) - t0).total_seconds() for s in samples], dtype=float)

    # Guard against duplicate timestamps in the feed.
    keep = np.concatenate([[True], np.diff(time_s) > 0])
    samples = [s for s, k in zip(samples, keep) if k]
    time_s = time_s[keep]

    warnings: list[str] = []

    speed = np.array([float(s.speed) if s.speed is not None else np.nan for s in samples])
    throttle = np.array([float(s.throttle) if s.throttle is not None else np.nan for s in samples])
    brake = np.array([float(s.brake) if s.brake is not None else np.nan for s in samples])
    rpm = np.array([float(s.rpm) if s.rpm is not None else np.nan for s in samples])
    gear = np.array([float(s.n_gear) if s.n_gear is not None else np.nan for s in samples])
    drs = np.array([float(s.drs) if s.drs is not None else np.nan for s in samples])

    for name, arr in (("throttle", throttle), ("brake", brake), ("rpm", rpm), ("n_gear", gear)):
        missing = int(np.isnan(arr).sum())
        if missing:
            warnings.append(f"{missing}/{arr.size} samples missing {name}")

    intervals = np.diff(time_s)
    median_interval = float(np.median(intervals)) if intervals.size else None
    if median_interval and intervals.size:
        big = int((intervals > median_interval * 4).sum())
        if big:
            warnings.append(
                f"{big} gap(s) longer than 4x the median sample interval "
                f"({median_interval:.2f}s) — distance integration drifts across these"
            )

    distance = integrate_distance(time_s, speed)

    x = y = None
    if locations:
        locs = sorted((l for l in locations if l.x is not None and l.y is not None), key=lambda l: l.date)
        if len(locs) >= 2:
            lt = np.array([(_as_utc(l.date) - t0).total_seconds() for l in locs], dtype=float)
            lmask = np.concatenate([[True], np.diff(lt) > 0])
            lt = lt[lmask]
            lx = np.array([float(l.x) for l in locs])[lmask]
            ly = np.array([float(l.y) for l in locs])[lmask]
            # Interpolate position onto the telemetry clock. Outside the
            # position feed's own span we leave NaN rather than extrapolating.
            x = np.interp(time_s, lt, lx, left=np.nan, right=np.nan)
            y = np.interp(time_s, lt, ly, left=np.nan, right=np.nan)
            covered = float(np.isfinite(x).mean())
            if covered < 0.95:
                warnings.append(f"position feed covers only {covered:.0%} of this lap's telemetry")
        else:
            warnings.append("position feed had too few usable samples for this lap")

    lap_duration = lap.lap_duration if lap is not None else None
    if lap_duration and time_s.size:
        covered = time_s[-1]
        if abs(covered - lap_duration) > max(1.0, 0.05 * lap_duration):
            warnings.append(
                f"telemetry spans {covered:.2f}s but the lap is timed at {lap_duration:.2f}s — "
                "the window may be clipped or include a neighbouring lap"
            )

    return LapTrace(
        driver_number=samples[0].driver_number,
        lap_number=lap.lap_number if lap is not None else None,
        session_key=session_key,
        time=time_s,
        distance=distance,
        speed=speed,
        throttle=throttle,
        brake=brake,
        rpm=rpm,
        n_gear=gear,
        drs=drs,
        x=x,
        y=y,
        lap_duration=lap_duration,
        sample_count=len(samples),
        median_interval_s=median_interval,
        warnings=warnings,
    )


def lap_window(lap: Lap, *, pad_before: float = 0.5, pad_after: float = 0.5) -> tuple[datetime, datetime] | None:
    """The UTC interval covering one timed lap.

    Padding exists so the trailing sample on either side is captured; the
    braking detector needs a little approach context before the line.
    """
    if lap.date_start is None or lap.lap_duration is None:
        return None
    start = _as_utc(lap.date_start)
    return (
        start - timedelta(seconds=pad_before),
        start + timedelta(seconds=float(lap.lap_duration) + pad_after),
    )


# ----------------------------------------------------------------------
# Pairwise alignment
# ----------------------------------------------------------------------


@dataclass
class AlignedPair:
    grid: np.ndarray
    a: LapTrace
    b: LapTrace
    mode: AlignmentMode
    reference_length: float
    length_a: float
    length_b: float
    notes: list[str] = field(default_factory=list)

    @property
    def length_discrepancy(self) -> float:
        return abs(self.length_a - self.length_b)


def align(
    a: LapTrace,
    b: LapTrace,
    *,
    mode: AlignmentMode = "truncate",
    step_m: float = 5.0,
) -> AlignedPair:
    """Put two laps on one distance grid.

    The grid step defaults to 5 m, finer than the ~22 m worst-case sample
    spacing. That is deliberate: a fine grid keeps the interpolation smooth for
    plotting, but it does not create information. Resolution claims must come
    from :meth:`LapTrace.spatial_resolution`, not from the grid.
    """
    la, lb = a.length, b.length
    notes: list[str] = []

    if la <= 0 or lb <= 0:
        raise ValueError("Cannot align a lap with zero integrated distance.")

    discrepancy = abs(la - lb)
    if discrepancy > 0.02 * max(la, lb):
        notes.append(
            f"integrated lap lengths differ by {discrepancy:.0f} m "
            f"({discrepancy / max(la, lb):.1%}) — larger than drift alone usually explains"
        )

    if mode == "normalize":
        reference = 0.5 * (la + lb)
        grid = np.arange(0.0, reference, step_m)
        a2 = _rescale(a, reference / la)
        b2 = _rescale(b, reference / lb)
        notes.append(
            f"distance axes scaled by {reference / la:.4f} and {reference / lb:.4f} onto a "
            f"{reference:.0f} m reference; this assumes the discrepancy is spread evenly "
            "around the lap"
        )
        return AlignedPair(grid, a2.resample(grid), b2.resample(grid), mode, reference, la, lb, notes)

    reference = min(la, lb)
    grid = np.arange(0.0, reference, step_m)
    if discrepancy > step_m:
        notes.append(f"comparison truncated at {reference:.0f} m, the shorter of the two traces")
    return AlignedPair(grid, a.resample(grid), b.resample(grid), mode, reference, la, lb, notes)


def _rescale(trace: LapTrace, factor: float) -> LapTrace:
    scaled = LapTrace(**{**trace.__dict__, "distance": trace.distance * factor})
    scaled.warnings = list(trace.warnings)
    return scaled
