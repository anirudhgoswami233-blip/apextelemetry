"""ApexTelemetry backend.

Run with:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .providers import ProviderError, shutdown_providers
from .routers import analysis, catalog, engineer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("apex")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info(
        "ApexTelemetry starting — provider=%s tier=%s live_access=%s cache=%s",
        settings.provider.name,
        settings.provider.tier,
        settings.provider.has_live_access,
        settings.cache_dir,
    )
    if not settings.provider.has_live_access:
        log.info(
            "No OPENF1_API_KEY set. Running on the Community tier: historical sessions from "
            "2023 onward, no live window. The UI will label every session accordingly."
        )
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    yield
    await shutdown_providers()


app = FastAPI(
    title="ApexTelemetry",
    description=(
        "Formula 1 telemetry, lap delta, corner and strategy analysis. "
        "Every response distinguishes measured, calculated and estimated values."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
    """Surface upstream problems as themselves, not as a generic 500.

    The frontend renders ``detail`` directly, so these messages are written for
    a human to read and act on.
    """
    return JSONResponse(
        status_code=exc.status or 502,
        content={
            "detail": str(exc),
            "retry_after": exc.retry_after,
            "source": "provider",
        },
    )


app.include_router(catalog.router)
app.include_router(analysis.router)
app.include_router(engineer.router)


@app.get("/")
async def root() -> dict:
    settings = get_settings()
    return {
        "name": "ApexTelemetry",
        "docs": "/docs",
        "provider": settings.provider.name,
        "live_access": settings.provider.has_live_access,
        "attribution": (
            "Data from OpenF1 (openf1.org). Unofficial and not affiliated with Formula 1, "
            "the FIA or Formula One Management. Educational and non-commercial use."
        ),
    }
