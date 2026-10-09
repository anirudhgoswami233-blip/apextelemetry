"""Catalog and live-timing routes: what exists, and what is happening now."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from ..providers import ProviderError, list_providers
from ..service import SessionService, jsonable

router = APIRouter(prefix="/api", tags=["catalog"])


def _handle(exc: ProviderError) -> HTTPException:
    return HTTPException(status_code=exc.status or 502, detail=str(exc))


@router.get("/health")
async def health() -> dict:
    service = SessionService()
    capabilities = service.provider.capabilities
    return {
        "status": "ok",
        "provider": capabilities.name,
        "live_access": capabilities.live,
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/providers")
async def providers() -> dict:
    """Every registered adapter and, importantly, what each one cannot do."""
    return {"providers": jsonable(list_providers())}


@router.get("/seasons")
async def seasons() -> dict:
    capabilities = SessionService().provider.capabilities
    first = capabilities.first_season or 2023
    last = capabilities.last_season or datetime.now(timezone.utc).year
    return {
        "seasons": list(range(last, first - 1, -1)),
        "note": f"{capabilities.display_name} covers {first} onward.",
    }


@router.get("/meetings")
async def meetings(year: int = Query(..., ge=1950, le=2100)) -> dict:
    try:
        return {"meetings": jsonable(await SessionService().provider.meetings(year))}
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/sessions")
async def sessions(
    year: int | None = Query(None, ge=1950, le=2100),
    meeting_key: int | None = None,
) -> dict:
    if year is None and meeting_key is None:
        raise HTTPException(400, "Provide either year or meeting_key.")
    try:
        found = await SessionService().provider.sessions(year=year, meeting_key=meeting_key)
        return {"sessions": jsonable(found)}
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/weekend/{meeting_key}")
async def weekend(meeting_key: int) -> dict:
    try:
        return await SessionService().weekend(meeting_key)
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/session/{session_key}/status")
async def status(session_key: int) -> dict:
    """Whether what you are about to see is live, historical, or archived."""
    try:
        return jsonable(await SessionService().feed_status(session_key))
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/session/{session_key}/drivers")
async def drivers(session_key: int) -> dict:
    try:
        found = await SessionService().provider.drivers(session_key)
        return {"drivers": jsonable(found)}
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/session/{session_key}/laps")
async def laps(session_key: int, driver_number: int | None = None) -> dict:
    """Laps, each annotated with why it may be unsuitable for a pace comparison."""
    from ..analytics import classify_laps

    service = SessionService()
    try:
        found = await service.provider.laps(session_key, driver_number)
        pit_stops = await service.provider.pit_stops(session_key)
        race_control = await service.provider.race_control(session_key)
    except ProviderError as exc:
        raise _handle(exc) from exc

    neutralised = {
        m.lap_number
        for m in race_control
        if m.lap_number is not None
        and ((m.flag or "").upper() in {"YELLOW", "DOUBLE YELLOW", "RED"}
             or "SAFETY CAR" in (m.message or "").upper())
    }

    out = []
    by_driver: dict[int, list] = {}
    for lap in found:
        by_driver.setdefault(lap.driver_number, []).append(lap)

    for number, driver_laps in by_driver.items():
        pit_laps = {p.lap_number for p in pit_stops if p.driver_number == number and p.lap_number}
        for quality in classify_laps(
            driver_laps, pit_laps=pit_laps, yellow_flag_laps=neutralised
        ):
            out.append(
                {
                    **jsonable(quality.lap),
                    "is_valid": quality.is_valid,
                    "flags": quality.flags,
                }
            )
    return {"laps": out}


@router.get("/session/{session_key}/timing")
async def timing(session_key: int) -> dict:
    """The live timing tower.

    Serving archived data through this route is allowed; claiming it is live is
    not. The ``status`` block says which it is, and the client must render it.
    """
    try:
        return await SessionService().timing_tower(session_key)
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/session/{session_key}/weather")
async def weather(session_key: int) -> dict:
    try:
        return {"weather": jsonable(await SessionService().provider.weather(session_key))}
    except ProviderError as exc:
        raise _handle(exc) from exc


@router.get("/session/{session_key}/race-control")
async def race_control(session_key: int) -> dict:
    try:
        return {"messages": jsonable(await SessionService().provider.race_control(session_key))}
    except ProviderError as exc:
        raise _handle(exc) from exc
