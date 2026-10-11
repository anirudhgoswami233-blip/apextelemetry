"""Orchestration between the provider and the analytics layer.

Routers stay thin: they parse query parameters and serialise results. Anything
that involves deciding *which* data to fetch, how much of it, and how to feed
it to the analytics lives here.

The recurring concern in this module is volume. Car telemetry is roughly 3.7
samples per second per car; a two-hour race across twenty cars is tens of
millions of samples. Nothing here ever fetches a whole session's telemetry.
Every telemetry read is scoped to one driver and the time window of specific
laps.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum

import numpy as np

from .analytics import (
    BrakeDetectionConfig,
    CircuitMap,
    CornerDetectionConfig,
    LapTrace,
    StrategyAssumptions,
    build_circuit_map,
    build_lap_trace,
    build_stints,
    classify_laps,
    compare_corner,
    compute_delta,
    corner_metrics,
    corner_metrics_for_lap,
    is_retired,
    last_timed_lap,
    tower_sort_key,
    visible_gaps,
    detect_brake_events,
    lap_window,
    summarise_pit_stops,
)
from .config import Settings, get_settings
from .models import Driver, FeedStatus, Lap, Session
from .providers import ProviderError, get_provider
from .providers.openf1 import OpenF1Provider


# ----------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------


def jsonable(value):
    """Convert dataclasses, numpy arrays and datetimes into JSON-safe types.

    Floats that numpy produces as NaN or infinity become ``None``: a missing
    measurement must arrive in the browser as null, never as the string "NaN"
    or a silent zero.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (np.floating,)):
        v = float(value)
        return v if np.isfinite(v) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.ndarray):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    return str(value)


def trace_payload(trace: LapTrace, *, every: int = 1) -> dict:
    """Serialise a trace as parallel arrays, which is what charts want.

    ``every`` thins the output for overview plots. Thinning is reported so the
    client never implies a precision it was not sent.
    """
    step = max(1, every)
    return {
        "driver_number": trace.driver_number,
        "lap_number": trace.lap_number,
        "lap_duration": trace.lap_duration,
        "length_m": trace.length,
        "sample_count": trace.sample_count,
        "sample_rate_hz": trace.sample_rate_hz,
        "spatial_resolution_m": jsonable(trace.spatial_resolution()),
        "decimated_by": step,
        "channels": {
            "distance": jsonable(trace.distance[::step]),
            "time": jsonable(trace.time[::step]),
            "speed": jsonable(trace.speed[::step]),
            "throttle": jsonable(trace.throttle[::step]),
            "brake": jsonable(trace.brake[::step]),
            "rpm": jsonable(trace.rpm[::step]),
            "gear": jsonable(trace.n_gear[::step]),
            "drs": jsonable(trace.drs[::step]),
            "x": jsonable(trace.x[::step]) if trace.x is not None else None,
            "y": jsonable(trace.y[::step]) if trace.y is not None else None,
        },
        "warnings": trace.warnings,
        "provenance": {
            "speed": "measured",
            "throttle": "measured",
            "brake": "measured",
            "rpm": "measured",
            "gear": "measured",
            "drs": "measured",
            "distance": "calculated (trapezoidal integration of speed over time)",
            "x": "measured",
            "y": "measured",
        },
    }


# ----------------------------------------------------------------------
# Session service
# ----------------------------------------------------------------------


class SessionService:
    def __init__(self, provider=None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or get_provider(settings=self.settings)

    # -- status ---------------------------------------------------------

    async def feed_status(self, session_key: int | None = None) -> FeedStatus:
        """Describe honestly what the client is about to look at."""
        caps = self.provider.capabilities
        session = await self.provider.session(session_key) if session_key else None

        in_window = (
            isinstance(self.provider, OpenF1Provider)
            and self.provider.is_live_window(session)
        )

        if in_window and caps.live:
            mode = "live"
            reason = None
        elif in_window and not caps.live:
            mode = "historical"
            reason = (
                "This session is inside the provider's live window, but this deployment has no "
                "live credential. You are seeing historical data only; it is not current."
            )
        else:
            mode = "archive"
            reason = None

        return FeedStatus(
            provider=caps.name,
            tier=getattr(self.settings.provider, "tier", "community"),
            mode=mode,
            live_access=caps.live,
            session_key=session_key,
            last_updated=datetime.now(timezone.utc),
            age_seconds=0.0,
            stale=False,
            degraded_reason=reason,
        )

    # -- catalog --------------------------------------------------------

    async def weekend(self, meeting_key: int) -> dict:
        sessions = await self.provider.sessions(meeting_key=meeting_key)
        now = datetime.now(timezone.utc)
        out = []
        for session in sessions:
            end = session.date_end
            completed = bool(end and end.replace(tzinfo=end.tzinfo or timezone.utc) < now)
            out.append(
                {
                    **jsonable(session),
                    "completed": completed,
                    "datasets": {
                        "telemetry": self.provider.capabilities.raw_telemetry,
                        "position": self.provider.capabilities.car_location,
                        "stints": self.provider.capabilities.stints,
                        "race_control": self.provider.capabilities.race_control,
                        "weather": self.provider.capabilities.weather,
                    },
                }
            )
        return {"meeting_key": meeting_key, "sessions": out}

    # -- traces ---------------------------------------------------------

    async def lap_trace(self, session_key: int, driver_number: int, lap_number: int) -> LapTrace:
        laps = await self.provider.laps(session_key, driver_number)
        match = next((l for l in laps if l.lap_number == lap_number), None)
        if match is None:
            raise ProviderError(f"Lap {lap_number} not found for car {driver_number}.", status=404)

        window = lap_window(match)
        if window is None:
            raise ProviderError(
                f"Lap {lap_number} has no start time or duration in the feed, so its telemetry "
                "window cannot be determined.",
                status=422,
            )

        start, end = window
        car, location = await asyncio.gather(
            self.provider.car_data(session_key, driver_number, start=start, end=end),
            self.provider.location(session_key, driver_number, start=start, end=end),
        )
        trace = build_lap_trace(car, lap=match, locations=location, session_key=session_key)
        if trace is None:
            raise ProviderError(
                f"No usable telemetry was returned for car {driver_number} on lap {lap_number}. "
                "Practice sessions in particular have gaps where a car was in the garage.",
                status=404,
            )
        return trace

    async def fastest_lap(self, session_key: int, driver_number: int) -> Lap | None:
        laps = await self.provider.laps(session_key, driver_number)
        timed = [l for l in laps if l.lap_duration is not None and not l.is_pit_out_lap]
        return min(timed, key=lambda l: l.lap_duration) if timed else None

    # -- comparison -----------------------------------------------------

    async def compare(
        self,
        session_key: int,
        driver_a: int,
        lap_a: int | None,
        driver_b: int,
        lap_b: int | None,
        *,
        mode: str = "truncate",
    ) -> dict:
        async def resolve(driver: int, number: int | None) -> int:
            if number is not None:
                return number
            best = await self.fastest_lap(session_key, driver)
            if best is None or best.lap_number is None:
                raise ProviderError(f"No timed lap found for car {driver}.", status=404)
            return best.lap_number

        lap_a_number, lap_b_number = await asyncio.gather(resolve(driver_a, lap_a), resolve(driver_b, lap_b))
        trace_a, trace_b = await asyncio.gather(
            self.lap_trace(session_key, driver_a, lap_a_number),
            self.lap_trace(session_key, driver_b, lap_b_number),
        )
        laps_a, laps_b = await asyncio.gather(
            self.provider.laps(session_key, driver_a),
            self.provider.laps(session_key, driver_b),
        )
        lap_model_a = next((l for l in laps_a if l.lap_number == lap_a_number), None)
        lap_model_b = next((l for l in laps_b if l.lap_number == lap_b_number), None)

        delta = compute_delta(
            trace_a, trace_b, mode=mode, lap_a=lap_model_a, lap_b=lap_model_b
        )
        return {
            "delta": jsonable(delta),
            "a": trace_payload(trace_a),
            "b": trace_payload(trace_b),
        }

    # -- corners --------------------------------------------------------

    async def circuit_map(
        self,
        session_key: int,
        *,
        reference_driver: int | None = None,
        config: CornerDetectionConfig | None = None,
    ) -> tuple[CircuitMap, LapTrace]:
        if reference_driver is None:
            laps = await self.provider.laps(session_key)
            timed = [l for l in laps if l.lap_duration is not None and not l.is_pit_out_lap]
            if not timed:
                raise ProviderError("No timed laps in this session to derive geometry from.", status=404)
            reference_driver = min(timed, key=lambda l: l.lap_duration).driver_number

        best = await self.fastest_lap(session_key, reference_driver)
        if best is None or best.lap_number is None:
            raise ProviderError(f"No timed lap for reference car {reference_driver}.", status=404)

        trace = await self.lap_trace(session_key, reference_driver, best.lap_number)
        return build_circuit_map(trace, config), trace

    async def corner_analysis(
        self,
        session_key: int,
        driver_a: int,
        driver_b: int,
        *,
        lap_a: int | None = None,
        lap_b: int | None = None,
        corner_config: CornerDetectionConfig | None = None,
        brake_config: BrakeDetectionConfig | None = None,
    ) -> dict:
        corner_config = corner_config or CornerDetectionConfig()
        brake_config = brake_config or BrakeDetectionConfig()

        comparison = await self.compare(session_key, driver_a, lap_a, driver_b, lap_b)
        trace_a = await self.lap_trace(
            session_key, driver_a, comparison["a"]["lap_number"]
        )
        trace_b = await self.lap_trace(
            session_key, driver_b, comparison["b"]["lap_number"]
        )

        circuit = build_circuit_map(trace_a, corner_config)
        events_a = detect_brake_events(trace_a, brake_config)
        events_b = detect_brake_events(trace_b, brake_config)

        distance = np.array(comparison["delta"]["distance"], dtype=float)
        values = np.array(
            [v if v is not None else np.nan for v in comparison["delta"]["delta"]], dtype=float
        )

        # Assign braking events across the whole lap rather than per corner, so
        # two corners close together cannot both claim the same event.
        metrics_a = corner_metrics_for_lap(
            trace_a, circuit.corners, config=corner_config, brake_events=events_a
        )
        metrics_b = corner_metrics_for_lap(
            trace_b, circuit.corners, config=corner_config, brake_events=events_b
        )

        rows = []
        for corner, m_a, m_b in zip(circuit.corners, metrics_a, metrics_b):
            rows.append(
                jsonable(
                    compare_corner(
                        corner,
                        m_a,
                        m_b,
                        delta_distance=distance,
                        delta_values=values,
                        config=corner_config,
                    )
                )
            )

        return {
            "circuit": jsonable(circuit),
            "comparison": rows,
            "method": {
                "corner_detection": corner_config.as_dict(),
                "brake_detection": brake_config.as_dict(),
                "explanation": {
                    "braking_point": (
                        "First sample where the brake flag goes high and stays high for at least "
                        "min_duration_s. Reported as the midpoint between the last unbraked and "
                        "first braked sample, with half that interval as uncertainty."
                    ),
                    "minimum_speed": "Lowest speed sample between the corner entry and exit distances.",
                    "apex": (
                        "Point of maximum path curvature on the reference racing line. An "
                        "approximation of the driver's apex, not a surveyed track feature."
                    ),
                    "time_delta": (
                        "Change in the cumulative lap delta across the corner window "
                        f"(entry minus {corner_config.approach_m:.0f} m to exit plus "
                        f"{corner_config.exit_m:.0f} m)."
                    ),
                    "confidence": (
                        "high when both braking points come from the brake channel and the "
                        "difference exceeds the combined sampling uncertainty; low when geometry "
                        "or the brake channel is unavailable."
                    ),
                },
            },
        }

    # -- strategy -------------------------------------------------------

    async def strategy(
        self, session_key: int, *, assumptions: StrategyAssumptions | None = None
    ) -> dict:
        assumptions = assumptions or StrategyAssumptions()
        stints, laps, pit_stops, race_control = await asyncio.gather(
            self.provider.stints(session_key),
            self.provider.laps(session_key),
            self.provider.pit_stops(session_key),
            self.provider.race_control(session_key),
        )
        neutralised = {
            m.lap_number
            for m in race_control
            if m.lap_number is not None
            and ((m.flag or "").upper() in {"YELLOW", "DOUBLE YELLOW", "RED"}
                 or "SAFETY CAR" in (m.message or "").upper())
        }
        summaries = build_stints(
            stints, laps, excluded_lap_numbers=neutralised, assumptions=assumptions
        )
        return {
            "stints": jsonable(summaries),
            "pit_stops": jsonable(summarise_pit_stops(pit_stops)),
            "neutralised_laps": sorted(neutralised),
            "assumptions": assumptions.as_dict(),
            "provenance": {
                "compound": "measured",
                "lap_times": "measured",
                "pit_duration": "measured",
                "degradation": "estimated (regression over tyre age, fuel-corrected by assumption)",
                "scenarios": "estimated",
            },
        }

    # -- timing tower ---------------------------------------------------

    async def timing_tower(self, session_key: int) -> dict:
        drivers, laps, stints, intervals, positions, race_control, weather, status = (
            await asyncio.gather(
                self.provider.drivers(session_key),
                self.provider.laps(session_key),
                self.provider.stints(session_key),
                self.provider.intervals(session_key),
                self.provider.positions(session_key),
                self.provider.race_control(session_key),
                self.provider.weather(session_key),
                self.feed_status(session_key),
            )
        )

        latest_position: dict[int, int] = {}
        for sample in positions:
            if sample.position is not None:
                latest_position[sample.driver_number] = sample.position

        latest_interval: dict[int, object] = {}
        for sample in intervals:
            latest_interval[sample.driver_number] = sample

        by_driver: dict[int, list[Lap]] = {}
        for lap in laps:
            by_driver.setdefault(lap.driver_number, []).append(lap)

        current_stint: dict[int, object] = {}
        for stint in stints:
            existing = current_stint.get(stint.driver_number)
            if existing is None or (stint.stint_number or 0) > (existing.stint_number or 0):
                current_stint[stint.driver_number] = stint

        session_best = min(
            (l.lap_duration for l in laps if l.lap_duration is not None), default=None
        )

        # A car that stopped early keeps its final gap and interval in the feed
        # forever. Rendering those as current makes a car fifty laps down look
        # like it is a second behind, so laps completed decides who is still
        # running and stale figures are suppressed for everyone else.
        leader_laps = max((len(l) for l in by_driver.values()), default=0)

        rows = []
        for driver in drivers:
            driver_laps = by_driver.get(driver.driver_number, [])
            timed = [l for l in driver_laps if l.lap_duration is not None]
            last = last_timed_lap(driver_laps)
            latest = driver_laps[-1] if driver_laps else None
            best = min(timed, key=lambda l: l.lap_duration) if timed else None
            retired = is_retired(len(driver_laps), leader_laps)
            stint = current_stint.get(driver.driver_number)
            interval = latest_interval.get(driver.driver_number)

            tyre_age = None
            if stint and stint.lap_start is not None and last and last.lap_number is not None:
                tyre_age = (stint.tyre_age_at_start or 0) + (last.lap_number - stint.lap_start)

            position = latest_position.get(driver.driver_number)
            gap_to_leader, gap_ahead = visible_gaps(
                retired=retired,
                position=position,
                gap_to_leader=getattr(interval, "gap_to_leader", None),
                interval=getattr(interval, "interval", None),
            )

            rows.append(
                {
                    "driver_number": driver.driver_number,
                    "acronym": driver.label,
                    "full_name": driver.full_name,
                    "team_name": driver.team_name,
                    "team_colour": driver.team_colour,
                    "position": position,
                    "laps_completed": len(driver_laps),
                    "last_lap": last.lap_duration if last else None,
                    "best_lap": best.lap_duration if best else None,
                    "is_session_best": bool(
                        best and session_best and abs(best.lap_duration - session_best) < 1e-6
                    ),
                    "sectors": last.sectors if last else [None, None, None],
                    "best_sectors": _best_sectors(timed),
                    "speed_trap": last.st_speed if last else None,
                    "gap_to_leader": gap_to_leader,
                    "interval": gap_ahead,
                    "compound": stint.compound if stint else None,
                    "stint_number": stint.stint_number if stint else None,
                    "tyre_age": tyre_age,
                    "tyre_age_provenance": "calculated" if tyre_age is not None else "unavailable",
                    "in_pit": bool(latest and latest.is_pit_out_lap),
                    "retired": retired,
                }
            )

        rows.sort(key=tower_sort_key)

        return {
            "status": jsonable(status),
            "drivers": rows,
            "race_control": jsonable(race_control[-40:]),
            "weather": jsonable(weather[-1]) if weather else None,
            "session_best": session_best,
        }


def _best_sectors(laps: list[Lap]) -> list[float | None]:
    out: list[float | None] = []
    for index in range(3):
        values = [l.sectors[index] for l in laps if l.sectors[index] is not None]
        out.append(min(values) if values else None)
    return out
