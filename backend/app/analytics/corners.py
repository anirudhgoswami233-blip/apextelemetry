"""Corner-by-corner engineering analysis.

**How corners are found.** The provider gives car positions in a circuit-local
x/y frame, not a corner list. We therefore derive the circuit's geometry from a
reference lap:

1. Resample the racing line onto a uniform distance grid.
2. Smooth it — the position feed is noisy at the metre scale, and raw
   differentiation of noise produces curvature that is pure artefact.
3. Compute signed curvature from the first and second derivatives of the path
   with respect to arc length:

       k = (x' y'' - y' x'') / (x'^2 + y'^2)^(3/2)

   whose magnitude is the reciprocal of the instantaneous radius.
4. Mark every stretch where |k| exceeds 1 / ``radius_threshold_m`` as a corner,
   merge stretches separated by less than ``merge_gap_m``, and discard anything
   shorter than ``min_length_m``.

**What the corner numbers are, and are not.** They are derived from one
driver's racing line on one lap, not from the circuit's official corner map.
They will usually agree with the official numbering on a conventional circuit
and will occasionally disagree — a long double-apex can register as one corner
or two depending on the threshold. The configuration is exposed for exactly
this reason, and corner boundaries can be overridden outright.

**The apex is an approximation.** We report the point of maximum curvature on
the chosen racing line. That is not the geometric apex of the track, nor the
kerb the driver clipped, and it moves with the line taken. It is labelled
``estimated`` everywhere it appears. Where track geometry is unavailable we do
not invent coordinates.

**Unit scale.** The position feed's units are not documented as metres. Rather
than assume, the scale is recovered by comparing the raw path length against
the distance obtained by integrating speed — two independent measurements of
the same lap. The resulting factor is reported so it can be sanity-checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .alignment import LapTrace
from .braking import (
    BrakeDetectionConfig,
    BrakeEvent,
    assign_brake_events,
    brake_event_before,
    detect_brake_events,
)


@dataclass(frozen=True)
class CornerDetectionConfig:
    radius_threshold_m: float = 300.0
    """A stretch counts as a corner below this instantaneous radius. Raise it
    to catch fast kinks, lower it to keep only genuine braking corners."""

    smoothing_window_m: float = 25.0
    merge_gap_m: float = 40.0
    min_length_m: float = 20.0
    grid_step_m: float = 5.0

    approach_m: float = 250.0
    """How far before the corner entry to include when attributing time."""

    exit_m: float = 150.0
    """How far past the corner exit to include when attributing time."""

    throttle_application_pct: float = 20.0
    full_throttle_pct: float = 95.0

    def as_dict(self) -> dict:
        return {
            "radius_threshold_m": self.radius_threshold_m,
            "smoothing_window_m": self.smoothing_window_m,
            "merge_gap_m": self.merge_gap_m,
            "min_length_m": self.min_length_m,
            "grid_step_m": self.grid_step_m,
            "approach_m": self.approach_m,
            "exit_m": self.exit_m,
            "throttle_application_pct": self.throttle_application_pct,
            "full_throttle_pct": self.full_throttle_pct,
        }


@dataclass
class Corner:
    number: int
    entry_m: float
    apex_m: float
    exit_m: float
    direction: str                     # "left" | "right"
    min_radius_m: float | None
    peak_curvature: float
    source: str = "derived-from-position"
    apex_provenance: str = "estimated"
    notes: list[str] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return self.exit_m - self.entry_m

    def window(self, config: CornerDetectionConfig) -> tuple[float, float]:
        return (max(0.0, self.entry_m - config.approach_m), self.exit_m + config.exit_m)


@dataclass
class CircuitMap:
    corners: list[Corner]
    x: np.ndarray | None
    y: np.ndarray | None
    distance: np.ndarray
    curvature: np.ndarray | None
    scale_factor: float | None
    lap_length_m: float
    reference_driver: int
    reference_lap: int | None
    config: dict
    method: str
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------


def _fill_gaps(values: np.ndarray) -> np.ndarray:
    """Linearly bridge NaN holes in a position channel.

    The position feed runs on its own clock, so interpolating it onto the
    telemetry clock leaves a NaN at each end of the lap where there is nothing
    to interpolate between. Those few samples must not be allowed to propagate:
    a single NaN turns the whole path-length sum into NaN, which previously
    defeated the unit-scale detection and left the circuit silently in the
    wrong units. Interior holes are bridged linearly; the ends are held flat.
    """
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if finite.all():
        return values
    if finite.sum() < 2:
        return values
    index = np.arange(values.size)
    return np.interp(index, index[finite], values[finite])


def _smooth(values: np.ndarray, window_pts: int) -> np.ndarray:
    if window_pts < 3 or values.size < window_pts:
        return values
    if window_pts % 2 == 0:
        window_pts += 1
    kernel = np.ones(window_pts) / window_pts
    padded = np.pad(values, window_pts // 2, mode="wrap")
    return np.convolve(padded, kernel, mode="valid")[: values.size]


def build_circuit_map(
    trace: LapTrace,
    config: CornerDetectionConfig | None = None,
) -> CircuitMap:
    config = config or CornerDetectionConfig()
    notes: list[str] = []

    grid = np.arange(0.0, trace.length, config.grid_step_m)
    if grid.size < 10:
        raise ValueError("Lap trace is too short to derive circuit geometry.")
    resampled = trace.resample(grid)

    has_position = (
        resampled.x is not None
        and resampled.y is not None
        and np.isfinite(resampled.x).mean() > 0.9
        and np.isfinite(resampled.y).mean() > 0.9
    )

    if not has_position:
        notes.append(
            "no usable car-position data for this lap — corners approximated from speed "
            "minima instead of track geometry, and no apex location is reported"
        )
        corners = _corners_from_speed(resampled, config)
        return CircuitMap(
            corners=corners,
            x=None,
            y=None,
            distance=grid,
            curvature=None,
            scale_factor=None,
            lap_length_m=trace.length,
            reference_driver=trace.driver_number,
            reference_lap=trace.lap_number,
            config=config.as_dict(),
            method="speed-minima-approximation",
            notes=notes,
        )

    window_pts = int(config.smoothing_window_m / config.grid_step_m)
    x = _smooth(_fill_gaps(resampled.x), window_pts)
    y = _smooth(_fill_gaps(resampled.y), window_pts)

    # Recover the position feed's unit scale from two independent measures of
    # the same lap: the raw path length, and the speed-integrated distance.
    raw_length = float(np.sum(np.hypot(np.diff(x), np.diff(y))))
    scale = (trace.length / raw_length) if np.isfinite(raw_length) and raw_length > 0 else None
    if scale is None:
        notes.append(
            "could not establish the position feed's unit scale from this lap — "
            "corner geometry is reported in raw feed units and radii are not meaningful"
        )
    if scale is not None:
        # ``scale`` converts feed units to metres, so a feed already in metres
        # gives ~1.0 and a feed in decimetres gives ~0.1.
        if 0.5 < scale < 2.0:
            notes.append(f"position units resolve to metres (scale {scale:.3f})")
        elif 0.05 < scale < 0.2:
            notes.append(f"position units appear to be decimetres (scale {scale:.3f})")
        else:
            notes.append(
                f"position-to-distance scale is {scale:.3f}, outside the expected range — "
                "treat corner geometry as approximate"
            )
        x = x * scale
        y = y * scale

    ds = config.grid_step_m
    dx, dy = np.gradient(x, ds), np.gradient(y, ds)
    ddx, ddy = np.gradient(dx, ds), np.gradient(dy, ds)

    denominator = np.power(dx**2 + dy**2, 1.5)
    with np.errstate(invalid="ignore", divide="ignore"):
        curvature = np.where(denominator > 1e-9, (dx * ddy - dy * ddx) / denominator, 0.0)
    curvature = _smooth(np.nan_to_num(curvature), int(config.smoothing_window_m / ds))

    threshold = 1.0 / config.radius_threshold_m
    corners = _corners_from_curvature(grid, curvature, threshold, config)

    if not corners:
        notes.append(
            f"no stretch tighter than {config.radius_threshold_m:.0f} m radius was found — "
            "lower radius_threshold_m if this circuit has only fast corners"
        )

    return CircuitMap(
        corners=corners,
        x=x,
        y=y,
        distance=grid,
        curvature=curvature,
        scale_factor=scale,
        lap_length_m=trace.length,
        reference_driver=trace.driver_number,
        reference_lap=trace.lap_number,
        config=config.as_dict(),
        method="curvature-of-racing-line",
        notes=notes,
    )


def _corners_from_curvature(
    distance: np.ndarray,
    curvature: np.ndarray,
    threshold: float,
    config: CornerDetectionConfig,
) -> list[Corner]:
    mask = np.abs(curvature) >= threshold
    runs = _runs(mask)

    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged and distance[start] - distance[merged[-1][1]] < config.merge_gap_m:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    # A lap is a closed loop, so a corner can straddle the start/finish line and
    # arrive as two fragments: one at distance ~0 and one at the end of the lap.
    # Stitching them back together stops a single corner being reported twice,
    # once as a stub with a nonsense radius.
    wraps = False
    lap_length = float(distance[-1])
    if len(merged) >= 2:
        first_start, first_end = merged[0]
        last_start, last_end = merged[-1]
        head = distance[first_start]
        tail = lap_length - distance[last_end]
        if head + tail < config.merge_gap_m:
            merged = merged[1:-1] + [(last_start, first_end)]
            wraps = True

    corners: list[Corner] = []
    for start, end in merged:
        wrapped = start > end
        if wrapped:
            indices = np.concatenate([np.arange(start, distance.size), np.arange(0, end + 1)])
            span = (lap_length - distance[start]) + distance[end]
        else:
            indices = np.arange(start, end + 1)
            span = distance[end] - distance[start]

        if span < config.min_length_m:
            continue

        segment = np.abs(curvature[indices])
        apex_index = int(indices[int(np.argmax(segment))])
        peak = float(segment.max())

        corner_notes = [
            "apex is the point of maximum curvature on this racing line, "
            "not a surveyed track feature"
        ]
        apex_m = float(distance[apex_index])
        exit_m = float(distance[end])
        if wrapped:
            corner_notes.append(
                "this corner crosses the start/finish line; exit distance continues past "
                "the lap length"
            )
            exit_m = lap_length + float(distance[end])
            if apex_index < start:
                apex_m = lap_length + apex_m

        corners.append(
            Corner(
                number=0,
                entry_m=float(distance[start]),
                apex_m=apex_m,
                exit_m=exit_m,
                direction="left" if curvature[apex_index] > 0 else "right",
                min_radius_m=float(1.0 / peak) if peak > 0 else None,
                peak_curvature=peak,
                notes=corner_notes,
            )
        )

    corners.sort(key=lambda c: c.entry_m)
    for index, corner in enumerate(corners, start=1):
        corner.number = index
    return corners


def _corners_from_speed(trace: LapTrace, config: CornerDetectionConfig) -> list[Corner]:
    """Fallback: treat sustained speed minima as corners.

    Deliberately coarse. It exists so a session without position data still
    gets something usable, clearly marked as an approximation and carrying no
    apex claim.
    """
    speed = _smooth(np.nan_to_num(trace.speed, nan=0.0), int(config.smoothing_window_m / config.grid_step_m))
    if speed.size < 10:
        return []
    ceiling = float(np.percentile(speed, 85))
    mask = speed < ceiling * 0.85

    corners: list[Corner] = []
    merged: list[tuple[int, int]] = []
    for start, end in _runs(mask):
        if merged and trace.distance[start] - trace.distance[merged[-1][1]] < config.merge_gap_m:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    for start, end in merged:
        if trace.distance[end] - trace.distance[start] < config.min_length_m:
            continue
        low = int(start + np.argmin(speed[start : end + 1]))
        corners.append(
            Corner(
                number=len(corners) + 1,
                entry_m=float(trace.distance[start]),
                apex_m=float(trace.distance[low]),
                exit_m=float(trace.distance[end]),
                direction="unknown",
                min_radius_m=None,
                peak_curvature=0.0,
                source="approximated-from-speed",
                apex_provenance="unavailable",
                notes=["no position data; 'apex' here is simply the slowest point"],
            )
        )
    return corners


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    if mask.size == 0 or not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1).tolist(), (np.flatnonzero(edges == -1) - 1).tolist()))


# ----------------------------------------------------------------------
# Per-driver corner metrics
# ----------------------------------------------------------------------


@dataclass
class CornerMetrics:
    corner: int
    driver_number: int
    lap_number: int | None

    brake: BrakeEvent | None
    entry_speed_kph: float | None
    min_speed_kph: float | None
    min_speed_distance_m: float | None
    exit_speed_kph: float | None

    throttle_application_m: float | None
    time_to_full_throttle_s: float | None
    gear_at_apex: int | None
    gear_range: tuple[int, int] | None
    drs_open: bool | None

    elapsed_through_window_s: float | None
    spatial_resolution_m: float
    notes: list[str] = field(default_factory=list)


def corner_metrics(
    trace: LapTrace,
    corner: Corner,
    *,
    config: CornerDetectionConfig | None = None,
    brake_config: BrakeDetectionConfig | None = None,
    brake_events: list[BrakeEvent] | None = None,
    assigned_brake: BrakeEvent | None = None,
    brake_assigned: bool = False,
) -> CornerMetrics:
    """Measure one corner.

    ``brake_assigned`` says the caller has already decided which braking event
    belongs to this corner (see :func:`corner_metrics_for_lap`), including
    deciding it has none. Without it this function looks the event up on its
    own, which is correct in isolation but lets two nearby corners claim the
    same braking event.
    """
    config = config or CornerDetectionConfig()
    events = brake_events if brake_events is not None else detect_brake_events(trace, brake_config)

    window_start, window_end = corner.window(config)
    window_end = min(window_end, trace.length)

    order = np.argsort(trace.distance, kind="stable")
    distance_sorted = np.maximum.accumulate(trace.distance[order])

    def at(distance: float, channel: str) -> float | None:
        values = trace.channel(channel)[order]
        finite = np.isfinite(values)
        if finite.sum() < 2:
            return None
        result = float(np.interp(distance, distance_sorted[finite], values[finite]))
        return result if np.isfinite(result) else None

    mask = (trace.distance >= corner.entry_m) & (trace.distance <= corner.exit_m)
    segment_speed = trace.speed[mask]
    segment_distance = trace.distance[mask]

    notes: list[str] = []
    if segment_speed.size < 3:
        notes.append(
            f"only {segment_speed.size} telemetry sample(s) fall inside this corner — "
            "minimum speed and apex figures are weakly constrained"
        )

    min_speed = float(np.nanmin(segment_speed)) if segment_speed.size and np.isfinite(segment_speed).any() else None
    min_distance = (
        float(segment_distance[int(np.nanargmin(segment_speed))])
        if segment_speed.size and np.isfinite(segment_speed).any()
        else None
    )

    if brake_assigned:
        brake = assigned_brake
        if brake is None:
            notes.append(
                "no separate braking event for this corner — the braking for this "
                "complex was attributed to the preceding corner"
            )
    else:
        brake = brake_event_before(events, corner.entry_m, window_m=config.approach_m)

    throttle_application = None
    time_to_full = None
    if min_distance is not None:
        after = trace.distance >= min_distance
        throttle_after = trace.throttle[after]
        distance_after = trace.distance[after]
        time_after = trace.time[after]
        applied = np.flatnonzero(np.nan_to_num(throttle_after, nan=0.0) >= config.throttle_application_pct)
        if applied.size:
            i = int(applied[0])
            throttle_application = float(distance_after[i])
            full = np.flatnonzero(np.nan_to_num(throttle_after[i:], nan=0.0) >= config.full_throttle_pct)
            if full.size:
                time_to_full = float(time_after[i + int(full[0])] - time_after[i])
            else:
                notes.append(
                    f"throttle never reached {config.full_throttle_pct:.0f}% before the lap ended"
                )

    gears = trace.n_gear[mask]
    gears = gears[np.isfinite(gears)]
    gear_range = (int(gears.min()), int(gears.max())) if gears.size else None
    gear_at_apex = None
    if corner.apex_provenance != "unavailable":
        value = at(corner.apex_m, "n_gear")
        gear_at_apex = int(round(value)) if value is not None else None

    drs_segment = trace.drs[mask]
    drs_segment = drs_segment[np.isfinite(drs_segment)]
    drs_open = bool(np.isin(drs_segment, [10, 12, 14]).any()) if drs_segment.size else None

    elapsed = None
    if window_end > window_start:
        t = trace.time_at(np.array([window_start, window_end]))
        elapsed = float(t[1] - t[0])

    return CornerMetrics(
        corner=corner.number,
        driver_number=trace.driver_number,
        lap_number=trace.lap_number,
        brake=brake,
        entry_speed_kph=at(corner.entry_m, "speed"),
        min_speed_kph=min_speed,
        min_speed_distance_m=min_distance,
        exit_speed_kph=at(corner.exit_m, "speed"),
        throttle_application_m=throttle_application,
        time_to_full_throttle_s=time_to_full,
        gear_at_apex=gear_at_apex,
        gear_range=gear_range,
        drs_open=drs_open,
        elapsed_through_window_s=elapsed,
        spatial_resolution_m=trace.spatial_resolution(corner.apex_m),
        notes=notes,
    )


def corner_metrics_for_lap(
    trace: LapTrace,
    corners: list[Corner],
    *,
    config: CornerDetectionConfig | None = None,
    brake_config: BrakeDetectionConfig | None = None,
    brake_events: list[BrakeEvent] | None = None,
) -> list[CornerMetrics]:
    """Measure every corner on one lap, sharing out braking events exclusively.

    This is the entry point callers should use. Measuring corners one at a time
    lets neighbouring corners report the same braking event twice; here the
    whole lap is considered at once, so each detected event belongs to exactly
    one corner.
    """
    config = config or CornerDetectionConfig()
    events = brake_events if brake_events is not None else detect_brake_events(trace, brake_config)

    assigned = assign_brake_events(
        [c.entry_m for c in corners], events, window_m=config.approach_m
    )

    return [
        corner_metrics(
            trace,
            corner,
            config=config,
            brake_events=events,
            assigned_brake=event,
            brake_assigned=True,
        )
        for corner, event in zip(corners, assigned)
    ]


# ----------------------------------------------------------------------
# Driver vs driver
# ----------------------------------------------------------------------


@dataclass
class CornerComparison:
    corner: int
    a: CornerMetrics
    b: CornerMetrics

    brake_point_difference_m: float | None
    brake_point_uncertainty_m: float | None
    brake_difference_significant: bool
    entry_speed_difference_kph: float | None
    min_speed_difference_kph: float | None
    exit_speed_difference_kph: float | None
    time_delta_s: float | None
    confidence: str
    interpretation: str
    notes: list[str] = field(default_factory=list)


def compare_corner(
    corner: Corner,
    a: CornerMetrics,
    b: CornerMetrics,
    *,
    delta_distance: np.ndarray | None = None,
    delta_values: np.ndarray | None = None,
    config: CornerDetectionConfig | None = None,
) -> CornerComparison:
    """Compare two drivers through one corner, without jumping to a verdict.

    ``brake_point_difference_m`` is positive when driver B braked **later**
    (further down the road). Crucially, that difference is only called
    significant when it exceeds the combined sample-spacing uncertainty of both
    traces — at 3.7 Hz a 15 m "difference" can be entirely an artefact of when
    the two cars happened to be sampled.
    """
    config = config or CornerDetectionConfig()
    notes: list[str] = []

    brake_difference = None
    brake_uncertainty = None
    significant = False
    if a.brake and b.brake:
        brake_difference = b.brake.onset_distance_m - a.brake.onset_distance_m
        brake_uncertainty = float(
            np.hypot(a.brake.onset_uncertainty_m, b.brake.onset_uncertainty_m)
        )
        significant = abs(brake_difference) > brake_uncertainty
        if not significant:
            notes.append(
                f"the {abs(brake_difference):.0f} m brake-point difference is within the "
                f"±{brake_uncertainty:.0f} m sampling uncertainty — not a distinguishable difference"
            )
        if a.brake.method != b.brake.method:
            notes.append("the two braking points were detected by different methods; not comparable")
            significant = False
    else:
        notes.append("no comparable braking event was detected for at least one driver")

    time_delta = None
    if delta_distance is not None and delta_values is not None and delta_distance.size:
        start, end = corner.window(config)
        end = min(end, float(delta_distance[-1]))
        if end > start:
            edges = np.interp([start, end], delta_distance, delta_values)
            time_delta = float(edges[1] - edges[0])

    def difference(x: float | None, y: float | None) -> float | None:
        return (y - x) if (x is not None and y is not None) else None

    entry_difference = difference(a.entry_speed_kph, b.entry_speed_kph)
    min_difference = difference(a.min_speed_kph, b.min_speed_kph)
    exit_difference = difference(a.exit_speed_kph, b.exit_speed_kph)

    confidence = _comparison_confidence(a, b, corner, significant)
    interpretation = _interpret(
        brake_difference if significant else None,
        min_difference,
        exit_difference,
        time_delta,
    )

    notes.extend(a.notes)
    notes.extend(b.notes)
    if corner.apex_provenance == "estimated":
        notes.append("corner boundaries derived from curvature, not an official circuit map")

    return CornerComparison(
        corner=corner.number,
        a=a,
        b=b,
        brake_point_difference_m=brake_difference,
        brake_point_uncertainty_m=brake_uncertainty,
        brake_difference_significant=significant,
        entry_speed_difference_kph=entry_difference,
        min_speed_difference_kph=min_difference,
        exit_speed_difference_kph=exit_difference,
        time_delta_s=time_delta,
        confidence=confidence,
        interpretation=interpretation,
        notes=notes,
    )


def _comparison_confidence(a: CornerMetrics, b: CornerMetrics, corner: Corner, significant: bool) -> str:
    resolution = max(a.spatial_resolution_m, b.spatial_resolution_m)
    if corner.source != "derived-from-position":
        return "low"
    if not (a.brake and b.brake):
        return "low"
    if a.brake.provenance == "estimated" or b.brake.provenance == "estimated":
        return "low"
    if resolution > corner.length_m / 2:
        return "low"
    if resolution <= 10 and significant:
        return "high"
    return "medium"


def _interpret(
    brake_difference: float | None,
    min_speed_difference: float | None,
    exit_speed_difference: float | None,
    time_delta: float | None,
) -> str:
    """Describe the corner without declaring anyone at fault.

    A later brake application is reported as a fact alongside its consequences.
    Whether it was a mistake depends on the time delta, which is stated rather
    than editorialised.
    """
    parts: list[str] = []

    if brake_difference is not None and abs(brake_difference) >= 1:
        who = "B" if brake_difference > 0 else "A"
        parts.append(f"driver {who} braked {abs(brake_difference):.0f} m later")
    else:
        parts.append("braking points are not distinguishable at this sample rate")

    if min_speed_difference is not None and abs(min_speed_difference) >= 1:
        who = "B" if min_speed_difference > 0 else "A"
        parts.append(f"driver {who} carried {abs(min_speed_difference):.1f} km/h more minimum speed")

    if exit_speed_difference is not None and abs(exit_speed_difference) >= 1:
        who = "B" if exit_speed_difference > 0 else "A"
        parts.append(f"driver {who} was {abs(exit_speed_difference):.1f} km/h faster at the exit")

    if time_delta is not None:
        if abs(time_delta) < 0.01:
            parts.append("net time through the corner is level")
        else:
            who = "A" if time_delta > 0 else "B"
            parts.append(f"driver {who} gained {abs(time_delta):.3f} s across the corner window")

    return "; ".join(parts) + "."
