"""A synthetic circuit and car, so the analytics can be tested against truth.

Real telemetry has no ground truth: nobody knows the exact metre at which a
driver touched the brakes, so a test against real data can only check that the
code does not crash. Here the circuit geometry, the speed profile and the
braking points are *constructed*, which means every detector can be checked
against the number it was supposed to find.

The generated data deliberately mimics the provider's limitations:
* samples at 3.7 Hz, the rate OpenF1 publishes car telemetry at,
* a brake channel that is 0 or 100 rather than a pressure trace,
* integer gears and DRS codes from the real code set,
* optional dropped samples and missing channels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from app.models import CarSample, Lap, LocationSample

SAMPLE_RATE_HZ = 3.7


@dataclass
class TrackSegment:
    length: float
    radius: float | None  # None => straight
    turn: int = 0  # +1 left, -1 right


def rounded_rectangle(
    straight_long: float = 700.0,
    straight_short: float = 350.0,
    radius: float = 90.0,
    line_on_corner: bool = False,
) -> list[TrackSegment]:
    """Four corners, two long straights, two short ones.

    By default the start/finish line sits halfway along a straight, as it does
    on essentially every real circuit. ``line_on_corner`` instead places it at
    a corner exit, which is the awkward case that makes one physical corner
    arrive as two fragments either side of the lap boundary.
    """
    quarter = 0.5 * np.pi * radius
    if line_on_corner:
        return [
            TrackSegment(straight_long, None),
            TrackSegment(quarter, radius, -1),
            TrackSegment(straight_short, None),
            TrackSegment(quarter, radius, -1),
            TrackSegment(straight_long, None),
            TrackSegment(quarter, radius, -1),
            TrackSegment(straight_short, None),
            TrackSegment(quarter, radius, -1),
        ]
    half = straight_long / 2.0
    return [
        TrackSegment(half, None),
        TrackSegment(quarter, radius, -1),
        TrackSegment(straight_short, None),
        TrackSegment(quarter, radius, -1),
        TrackSegment(straight_long, None),
        TrackSegment(quarter, radius, -1),
        TrackSegment(straight_short, None),
        TrackSegment(quarter, radius, -1),
        TrackSegment(half, None),
    ]


def linked_corner_circuit(gap_m: float = 40.0, radius: float = 60.0) -> list[TrackSegment]:
    """A circuit with two corners almost joined together.

    This is the shape that broke braking-point attribution on the real data:
    Singapore's T5 and T6 sit close enough that one application of the brakes
    serves both, and a per-corner lookup reported the same braking point twice.
    ``gap_m`` is the straight between them.
    """
    q = 0.5 * np.pi * radius
    return [
        TrackSegment(600.0, None),
        TrackSegment(q, radius, -1),
        TrackSegment(gap_m, None),
        TrackSegment(q, radius, -1),
        TrackSegment(700.0, None),
        TrackSegment(q, radius + 10, -1),
        TrackSegment(300.0, None),
        TrackSegment(q, radius + 10, -1),
        TrackSegment(200.0, None),
    ]


def build_path(segments: list[TrackSegment], step: float = 1.0):
    """Walk the segment list, returning distance, x, y and curvature arrays."""
    distances, xs, ys, curvatures = [0.0], [0.0], [0.0], [0.0]
    heading = 0.0
    x = y = s = 0.0

    for segment in segments:
        n = max(2, int(round(segment.length / step)))
        ds = segment.length / n
        kappa = (segment.turn / segment.radius) if segment.radius else 0.0
        for _ in range(n):
            heading += kappa * ds
            x += ds * np.cos(heading)
            y += ds * np.sin(heading)
            s += ds
            distances.append(s)
            xs.append(x)
            ys.append(y)
            curvatures.append(kappa)

    return (
        np.array(distances),
        np.array(xs),
        np.array(ys),
        np.array(curvatures),
    )


def speed_profile(
    curvature: np.ndarray,
    distance: np.ndarray,
    *,
    v_max_kph: float = 330.0,
    lateral_g: float = 4.0,
    brake_g: float = 4.5,
    accel_g: float = 1.6,
) -> np.ndarray:
    """A physically-shaped speed trace: cornering limit, then braking and
    traction limits applied by a backward and forward pass."""
    g = 9.81
    v_max = v_max_kph / 3.6

    with np.errstate(divide="ignore"):
        limit = np.where(
            np.abs(curvature) > 1e-9,
            np.sqrt(lateral_g * g / np.maximum(np.abs(curvature), 1e-9)),
            v_max,
        )
    v = np.minimum(limit, v_max)

    ds = np.diff(distance)

    # Backward pass: you can only arrive at a corner as fast as you can brake to.
    for i in range(v.size - 2, -1, -1):
        v[i] = min(v[i], float(np.sqrt(v[i + 1] ** 2 + 2 * brake_g * g * ds[i])))
    # Forward pass: you can only leave a corner as fast as traction allows.
    for i in range(1, v.size):
        v[i] = min(v[i], float(np.sqrt(v[i - 1] ** 2 + 2 * accel_g * g * ds[i - 1])))

    return v * 3.6  # back to km/h


def true_brake_zones(distance: np.ndarray, speed_kph: np.ndarray, *, threshold_mps2: float = -3.0):
    """Ground-truth braking zones: where the constructed profile decelerates."""
    v = speed_kph / 3.6
    dv_ds = np.gradient(v, distance)
    accel = v * dv_ds  # dv/dt = v * dv/ds
    mask = accel <= threshold_mps2

    zones: list[tuple[float, float]] = []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1):
        zones.append((float(distance[start]), float(distance[end])))
    return zones


def sample_lap(
    *,
    segments: list[TrackSegment] | None = None,
    driver_number: int = 44,
    session_key: int = 9999,
    lap_number: int = 12,
    start: datetime | None = None,
    speed_scale: float = 1.0,
    rate_hz: float = SAMPLE_RATE_HZ,
    jitter_s: float = 0.0,
    drop_fraction: float = 0.0,
    include_brake: bool = True,
    include_position: bool = True,
    seed: int = 0,
):
    """Produce provider-shaped samples for one lap, plus the ground truth."""
    rng = np.random.default_rng(seed)
    segments = segments or rounded_rectangle()
    distance, x, y, curvature = build_path(segments)
    speed = speed_profile(curvature, distance) * speed_scale

    # Time at each metre, from integrating 1/v.
    v_ms = np.maximum(speed / 3.6, 1.0)
    dt = np.diff(distance) / (0.5 * (v_ms[1:] + v_ms[:-1]))
    time = np.concatenate([[0.0], np.cumsum(dt)])
    lap_duration = float(time[-1])

    # Resample onto the provider's clock.
    sample_times = np.arange(0.0, lap_duration, 1.0 / rate_hz)
    if sample_times[-1] < lap_duration - 1e-9:
        # Carry the lap all the way to the line; otherwise the generator itself
        # clips the final fraction of a second and any length check is really
        # measuring the fixture, not the code under test.
        sample_times = np.append(sample_times, lap_duration)
    n = sample_times.size
    if jitter_s:
        sample_times = np.sort(sample_times + rng.normal(0, jitter_s, n))
        sample_times = np.clip(sample_times, 0, lap_duration)

    if drop_fraction:
        keep = rng.random(sample_times.size) > drop_fraction
        sample_times = sample_times[keep]

    sampled_speed = np.interp(sample_times, time, speed)
    sampled_distance = np.interp(sample_times, time, distance)
    sampled_x = np.interp(sample_times, time, x)
    sampled_y = np.interp(sample_times, time, y)

    # Brake flag from the ground-truth deceleration.
    accel = np.gradient(speed / 3.6, time)
    sampled_accel = np.interp(sample_times, time, accel)
    brake_flag = np.where(sampled_accel <= -3.0, 100.0, 0.0)

    # Throttle: full when accelerating, zero under braking, partial between.
    throttle = np.clip(np.interp(sample_times, time, accel) * 40 + 60, 0, 100)
    throttle = np.where(brake_flag > 0, 0.0, throttle)

    gear = np.clip(np.round(sampled_speed / 40.0), 1, 8).astype(int)
    rpm = 4000 + (sampled_speed / 330.0) * 8000

    # DRS open only on the long straight at high speed, using real DRS codes.
    drs = np.where((sampled_speed > 280) & (throttle > 95), 12, 0).astype(int)

    start = start or datetime(2026, 3, 8, 14, 5, 0, tzinfo=timezone.utc)

    car_samples = [
        CarSample(
            date=start + timedelta(seconds=float(t)),
            driver_number=driver_number,
            speed=float(sp),
            throttle=float(th),
            brake=float(br) if include_brake else None,
            rpm=float(r),
            n_gear=int(g),
            drs=int(d),
        )
        for t, sp, th, br, r, g, d in zip(
            sample_times, sampled_speed, throttle, brake_flag, rpm, gear, drs
        )
    ]

    location_samples = []
    if include_position:
        # Position feed runs on its own clock, offset from the telemetry one.
        location_times = sample_times + 0.11
        location_samples = [
            LocationSample(
                date=start + timedelta(seconds=float(t)),
                driver_number=driver_number,
                # Published in decimetres, to exercise the scale detection.
                x=float(np.interp(t, time, x) * 10.0),
                y=float(np.interp(t, time, y) * 10.0),
                z=0.0,
            )
            for t in location_times
        ]

    lap = Lap(
        driver_number=driver_number,
        lap_number=lap_number,
        session_key=session_key,
        date_start=start,
        lap_duration=lap_duration,
        duration_sector_1=lap_duration / 3,
        duration_sector_2=lap_duration / 3,
        duration_sector_3=lap_duration / 3,
        is_pit_out_lap=False,
    )

    truth = {
        "lap_length_m": float(distance[-1]),
        "lap_duration_s": lap_duration,
        "corner_count": sum(1 for s in segments if s.radius is not None),
        "brake_zones": true_brake_zones(distance, speed),
        "distance": distance,
        "speed": speed,
        "curvature": curvature,
        "sampled_distance": sampled_distance,
    }
    return car_samples, location_samples, lap, truth
