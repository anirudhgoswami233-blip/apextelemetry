"""Telemetry, lap delta, corner and strategy routes."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..analytics import BrakeDetectionConfig, CornerDetectionConfig, StrategyAssumptions
from ..analytics.stints import safety_car_scenario, undercut_scenario
from ..providers import ProviderError
from ..service import SessionService, jsonable, trace_payload

router = APIRouter(prefix="/api", tags=["analysis"])


def _handle(exc: ProviderError) -> HTTPException:
    return HTTPException(status_code=exc.status or 502, detail=str(exc))


@router.get("/session/{session_key}/telemetry")
async def telemetry(
    session_key: int,
    driver_number: int,
    lap_number: int | None = None,
    decimate: int = Query(1, ge=1, le=20),
) -> dict:
    """One lap of telemetry on a distance axis. Omit ``lap_number`` for the best lap."""
    service = SessionService()
    try:
        if lap_number is None:
            best = await service.fastest_lap(session_key, driver_number)
            if best is None or best.lap_number is None:
                raise HTTPException(404, f"No timed lap found for car {driver_number}.")
            lap_number = best.lap_number
        trace = await service.lap_trace(session_key, driver_number, lap_number)
    except ProviderError as exc:
        raise _handle(exc) from exc
    return trace_payload(trace, every=decimate)


@router.get("/session/{session_key}/compare")
async def compare(
    session_key: int,
    driver_a: int,
    driver_b: int,
    lap_a: int | None = None,
    lap_b: int | None = None,
    mode: str = Query("truncate", pattern="^(truncate|normalize)$"),
) -> dict:
    """Synchronised traces plus the cumulative lap delta."""
    try:
        return await SessionService().compare(
            session_key, driver_a, lap_a, driver_b, lap_b, mode=mode
        )
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/session/{session_key}/compare.csv")
async def compare_csv(
    session_key: int,
    driver_a: int,
    driver_b: int,
    lap_a: int | None = None,
    lap_b: int | None = None,
    mode: str = Query("truncate", pattern="^(truncate|normalize)$"),
) -> StreamingResponse:
    """The same comparison as CSV, for taking into a spreadsheet or notebook."""
    try:
        result = await SessionService().compare(
            session_key, driver_a, lap_a, driver_b, lap_b, mode=mode
        )
    except ProviderError as exc:
        raise _handle(exc) from exc

    delta = result["delta"]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            f"# ApexTelemetry export - session {session_key}, "
            f"car {driver_a} lap {result['a']['lap_number']} vs "
            f"car {driver_b} lap {result['b']['lap_number']}"
        ]
    )
    writer.writerow([f"# alignment={delta['alignment_mode']}, "
                     f"spatial_resolution_m={delta['spatial_resolution_m']}"])
    writer.writerow(["# distance is calculated by integrating speed; it is not a measured channel"])
    writer.writerow(["distance_m", "delta_s", "speed_difference_kph"])
    for d, dl, sd in zip(delta["distance"], delta["delta"], delta["speed_difference"]):
        writer.writerow([d, dl, sd])

    buffer.seek(0)
    filename = f"apex_{session_key}_{driver_a}v{driver_b}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/session/{session_key}/circuit")
async def circuit(
    session_key: int,
    reference_driver: int | None = None,
    radius_threshold_m: float = Query(300.0, gt=10.0, le=2000.0),
    smoothing_window_m: float = Query(25.0, gt=0.0, le=200.0),
    min_length_m: float = Query(20.0, gt=0.0, le=500.0),
) -> dict:
    """The circuit map with numbered corners, derived from a reference lap."""
    config = CornerDetectionConfig(
        radius_threshold_m=radius_threshold_m,
        smoothing_window_m=smoothing_window_m,
        min_length_m=min_length_m,
    )
    try:
        circuit_map, trace = await SessionService().circuit_map(
            session_key, reference_driver=reference_driver, config=config
        )
    except ProviderError as exc:
        raise _handle(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    return {"circuit": jsonable(circuit_map), "reference_trace": trace_payload(trace, every=2)}


@router.get("/session/{session_key}/corners")
async def corners(
    session_key: int,
    driver_a: int,
    driver_b: int,
    lap_a: int | None = None,
    lap_b: int | None = None,
    radius_threshold_m: float = Query(300.0, gt=10.0, le=2000.0),
    brake_method: str = Query("auto", pattern="^(auto|flag|deceleration)$"),
    brake_threshold: float = Query(50.0, ge=0.0, le=100.0),
    decel_threshold_mps2: float = Query(-3.0, le=0.0, ge=-20.0),
    approach_m: float = Query(250.0, ge=0.0, le=1000.0),
) -> dict:
    """Corner-by-corner comparison, with the detection settings echoed back."""
    try:
        return await SessionService().corner_analysis(
            session_key,
            driver_a,
            driver_b,
            lap_a=lap_a,
            lap_b=lap_b,
            corner_config=CornerDetectionConfig(
                radius_threshold_m=radius_threshold_m, approach_m=approach_m
            ),
            brake_config=BrakeDetectionConfig(
                method=brake_method,  # type: ignore[arg-type]
                flag_threshold=brake_threshold,
                decel_threshold_mps2=decel_threshold_mps2,
            ),
        )
    except ProviderError as exc:
        raise _handle(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/session/{session_key}/strategy")
async def strategy(
    session_key: int,
    fuel_effect_s_per_lap: float = Query(0.055, ge=0.0, le=0.5),
    pit_lane_loss_s: float = Query(21.0, ge=0.0, le=60.0),
) -> dict:
    """Stints, degradation fits and pit-stop comparison."""
    try:
        return await SessionService().strategy(
            session_key,
            assumptions=StrategyAssumptions(
                fuel_effect_s_per_lap=fuel_effect_s_per_lap, pit_lane_loss_s=pit_lane_loss_s
            ),
        )
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/scenario/undercut")
async def undercut(
    fresh_tyre_gain_s_per_lap: float = Query(..., ge=-5.0, le=5.0),
    laps_until_rival_pits: int = Query(..., ge=1, le=20),
    degradation_s_per_lap: float | None = None,
    extra_laps_on_old_tyre_at_end: int = Query(0, ge=0, le=40),
    pit_lane_loss_s: float = Query(21.0, ge=0.0, le=60.0),
    out_lap_penalty_s: float = Query(1.2, ge=0.0, le=10.0),
) -> dict:
    """A transparent undercut model: every term named, nothing hidden."""
    return jsonable(
        undercut_scenario(
            fresh_tyre_gain_s_per_lap=fresh_tyre_gain_s_per_lap,
            laps_until_rival_pits=laps_until_rival_pits,
            degradation_s_per_lap=degradation_s_per_lap,
            extra_laps_on_old_tyre_at_end=extra_laps_on_old_tyre_at_end,
            assumptions=StrategyAssumptions(
                pit_lane_loss_s=pit_lane_loss_s, out_lap_penalty_s=out_lap_penalty_s
            ),
        )
    )


@router.get("/scenario/safety-car")
async def safety_car(
    laps_remaining: int = Query(..., ge=1, le=80),
    current_tyre_age: int = Query(..., ge=0, le=80),
    degradation_s_per_lap: float | None = None,
    safety_car_pit_saving_s: float = Query(10.0, ge=0.0, le=30.0),
) -> dict:
    return jsonable(
        safety_car_scenario(
            laps_remaining=laps_remaining,
            current_tyre_age=current_tyre_age,
            degradation_s_per_lap=degradation_s_per_lap,
            assumptions=StrategyAssumptions(safety_car_pit_saving_s=safety_car_pit_saving_s),
        )
    )
