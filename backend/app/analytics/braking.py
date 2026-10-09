"""Brake-onset detection.

The whole corner analysis rests on one question — *where did the driver get on
the brakes?* — so the method is documented, configurable, and reports its own
uncertainty rather than returning a bare number.

**Two detection methods.**

``flag``
    OpenF1's ``brake`` channel is a flag, not a pressure trace: it reads 0 or
    100. Onset is the first sample where it goes high and stays high for at
    least ``min_duration_s``. Because the channel is binary and sampled at
    about 3.7 Hz, the true onset lies somewhere in the interval between the
    last low sample and the first high one. We report the **midpoint of that
    interval** and carry ±half the interval as the uncertainty. At 300 km/h
    that is roughly ±11 m. This is a hard floor set by the provider's sample
    rate; no amount of processing improves it.

``deceleration``
    A fallback for when the brake channel is absent. Speed is smoothed, then
    differentiated, and onset is the first point where longitudinal
    deceleration passes ``decel_threshold_mps2``. This is *estimated*, not
    measured: a lift-and-coast, a downshift, or drag at the end of a straight
    all produce deceleration without brake application. Results from this
    method are labelled accordingly and should never be presented as a braking
    point without that caveat.

**What this module deliberately does not do.** It does not conclude that
anybody braked too late. A later brake-onset distance is a fact; whether it
helped depends on entry speed, minimum speed, exit speed, and the elapsed time
through the whole corner complex. That judgement lives in ``corners.py``, where
all of those are available together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .alignment import LapTrace

Method = Literal["flag", "deceleration", "auto"]
Confidence = Literal["high", "medium", "low", "none"]


@dataclass(frozen=True)
class BrakeDetectionConfig:
    """Every knob the detector exposes. Serialised into each result."""

    method: Method = "auto"

    flag_threshold: float = 50.0
    """Brake channel value at or above which the brakes count as applied.
    OpenF1 emits 0 or 100, so anything in (0, 100) behaves identically; the
    parameter exists so a pressure-trace provider can be plugged in later."""

    decel_threshold_mps2: float = -3.0
    """Longitudinal deceleration marking brake onset in the fallback method.
    An F1 car coasting off-throttle at high speed decelerates at roughly
    1-2 m/s² from drag alone, so -3.0 sits clear of that without needing a
    hard stab of the pedal."""

    min_duration_s: float = 0.25
    """Reject single-sample spikes. One sample at 3.7 Hz is ~0.27 s, so this
    effectively requires a second corroborating sample."""

    min_speed_drop_kph: float = 15.0
    """Reject brushes of the pedal that do not meaningfully slow the car."""

    smoothing_window_m: float = 15.0
    """Distance-domain smoothing applied to speed before differentiating.
    Applied only in the deceleration method; the flag method never smooths,
    because smoothing a binary channel would move the edge it is trying to
    find."""

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "flag_threshold": self.flag_threshold,
            "decel_threshold_mps2": self.decel_threshold_mps2,
            "min_duration_s": self.min_duration_s,
            "min_speed_drop_kph": self.min_speed_drop_kph,
            "smoothing_window_m": self.smoothing_window_m,
        }


@dataclass
class BrakeEvent:
    onset_distance_m: float
    onset_uncertainty_m: float
    onset_time_s: float
    speed_at_onset_kph: float | None

    release_distance_m: float | None = None
    duration_s: float | None = None
    min_speed_kph: float | None = None
    min_speed_distance_m: float | None = None
    speed_drop_kph: float | None = None

    method: str = "flag"
    provenance: str = "calculated"
    confidence: Confidence = "medium"
    notes: list[str] = field(default_factory=list)

    @property
    def onset_range_m(self) -> tuple[float, float]:
        return (
            self.onset_distance_m - self.onset_uncertainty_m,
            self.onset_distance_m + self.onset_uncertainty_m,
        )


def _confidence(uncertainty_m: float, method: str, channel_coverage: float) -> Confidence:
    if channel_coverage < 0.5:
        return "low"
    if method == "deceleration":
        return "low" if uncertainty_m > 20 else "medium"
    if uncertainty_m <= 10:
        return "high"
    if uncertainty_m <= 20:
        return "medium"
    return "low"


def _smooth_by_distance(distance: np.ndarray, values: np.ndarray, window_m: float) -> np.ndarray:
    if distance.size < 3 or window_m <= 0:
        return values
    spacing = float(np.median(np.diff(distance)))
    if spacing <= 0:
        return values
    width = int(round(window_m / spacing))
    if width < 3:
        return values
    if width % 2 == 0:
        width += 1
    filled = np.nan_to_num(values, nan=float(np.nanmean(values)) if np.isfinite(values).any() else 0.0)
    kernel = np.ones(width) / width
    padded = np.pad(filled, width // 2, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def detect_brake_events(
    trace: LapTrace,
    config: BrakeDetectionConfig | None = None,
) -> list[BrakeEvent]:
    """Find every braking event on a lap, in distance order."""
    config = config or BrakeDetectionConfig()
    if trace.distance.size < 3:
        return []

    brake_coverage = float(np.isfinite(trace.brake).mean()) if trace.brake.size else 0.0
    method = config.method
    if method == "auto":
        method = "flag" if brake_coverage >= 0.5 else "deceleration"

    if method == "flag" and brake_coverage < 0.5:
        return []

    if method == "flag":
        applied = np.nan_to_num(trace.brake, nan=0.0) >= config.flag_threshold
    else:
        speed_ms = _smooth_by_distance(trace.distance, trace.speed, config.smoothing_window_m) / 3.6
        with np.errstate(invalid="ignore", divide="ignore"):
            accel = np.gradient(speed_ms, trace.time)
        applied = np.nan_to_num(accel, nan=0.0) <= config.decel_threshold_mps2

    events: list[BrakeEvent] = []
    for start, end in _runs(applied):
        onset_time = float(trace.time[start])
        duration = float(trace.time[end] - trace.time[start])

        # A run that is a single sample has no measurable duration; judge it by
        # the gap to the next sample instead of rejecting it outright.
        effective = duration if duration > 0 else _gap(trace.time, end)
        if effective < config.min_duration_s:
            continue

        segment = trace.speed[start : end + 1]
        entry_speed = float(trace.speed[start]) if np.isfinite(trace.speed[start]) else None
        min_speed = float(np.nanmin(segment)) if np.isfinite(segment).any() else None
        drop = (entry_speed - min_speed) if (entry_speed is not None and min_speed is not None) else None
        if drop is not None and drop < config.min_speed_drop_kph:
            continue

        # Locate the onset inside the interval between the last unbraked sample
        # and the first braked one, and carry half that interval as error.
        if start > 0:
            lo_d, hi_d = float(trace.distance[start - 1]), float(trace.distance[start])
            onset_distance = 0.5 * (lo_d + hi_d)
            uncertainty = 0.5 * (hi_d - lo_d)
            onset_time = 0.5 * (float(trace.time[start - 1]) + float(trace.time[start]))
        else:
            onset_distance = float(trace.distance[start])
            uncertainty = float(trace.spatial_resolution(onset_distance)) / 2.0

        notes: list[str] = []
        if start == 0:
            notes.append("braking was already active at the first sample of this window")
        if method == "deceleration":
            notes.append(
                "derived from deceleration, not a brake channel — a lift or downshift can "
                "produce the same signature"
            )

        min_index = (
            int(start + np.nanargmin(segment)) if np.isfinite(segment).any() else None
        )

        events.append(
            BrakeEvent(
                onset_distance_m=onset_distance,
                onset_uncertainty_m=uncertainty,
                onset_time_s=onset_time,
                speed_at_onset_kph=entry_speed,
                release_distance_m=float(trace.distance[end]),
                duration_s=duration if duration > 0 else None,
                min_speed_kph=min_speed,
                min_speed_distance_m=float(trace.distance[min_index]) if min_index is not None else None,
                speed_drop_kph=drop,
                method=method,
                provenance="calculated" if method == "flag" else "estimated",
                confidence=_confidence(uncertainty, method, brake_coverage),
                notes=notes,
            )
        )
    return events


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive index ranges of contiguous True values."""
    if mask.size == 0 or not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def _gap(time: np.ndarray, index: int) -> float:
    if time.size < 2:
        return 0.0
    if index + 1 < time.size:
        return float(time[index + 1] - time[index])
    return float(time[index] - time[index - 1])


def brake_event_before(events: list[BrakeEvent], distance_m: float, *, window_m: float = 400.0) -> BrakeEvent | None:
    """The braking event most likely to belong to a corner at ``distance_m``.

    ``window_m`` bounds how far back to look. A 400 m approach covers even the
    longest heavy-braking zones on the calendar without reaching back into the
    previous corner on a tight circuit.
    """
    candidates = [
        e for e in events if distance_m - window_m <= e.onset_distance_m <= distance_m
    ]
    return max(candidates, key=lambda e: e.onset_distance_m) if candidates else None
