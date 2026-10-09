"""The AI race engineer.

The rule this module exists to enforce: **the model never sees the question
without the data, and never answers from general Formula 1 knowledge.**

Concretely, a request here does not reach a language model until the backend
has computed an evidence bundle — real laps, real deltas, real corner metrics,
each carrying its provenance and uncertainty. The model's only job is to turn
that bundle into prose. It is explicitly instructed that any figure it states
must appear in the bundle, and that the correct answer to an unsupported
question is to say the data does not support one.

If no model credential is configured the endpoint still works: it returns the
evidence bundle on its own. That is deliberate. The evidence is the valuable
part; the narration is a convenience.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..providers import ProviderError
from ..service import SessionService

router = APIRouter(prefix="/api/engineer", tags=["engineer"])

SYSTEM_PROMPT = """You are a Formula 1 performance engineer writing for other engineers.

You will be given a QUESTION and an EVIDENCE bundle computed from session telemetry.

Rules you must follow without exception:

1. Every number you state must appear in the EVIDENCE bundle. Do not calculate new
   figures, do not round away meaningful precision, and never recall a figure from
   general knowledge of Formula 1.
2. The bundle marks each value's provenance. "measured" came from the timing feed.
   "calculated" is arithmetic over measured values. "estimated" depends on a model
   with assumptions. Say which you are relying on when it matters, and never present
   an estimate as a measurement.
3. Respect stated uncertainty. If a braking-point difference is marked as not
   significant, do not describe the drivers as braking at different points. If a
   degradation fit is marked as not meaningful, do not quote its slope as a fact.
4. Never claim a driver made a mistake, braked too late, or was at fault. Describe
   what the data shows and what it is consistent with.
5. Never invent radio messages, race events, weather, or engineering explanations
   that are not in the bundle.
6. If the bundle does not contain what is needed to answer, say exactly that and
   name what would be required. A short honest answer beats a confident wrong one.
7. Cite as you go, in square brackets, referring to the bundle: [corner 7],
   [sector 2], [stint 3], [lap 18]. Keep it readable, not a wall of citations.

Write tightly. An engineer's attention is the scarce resource."""


class EngineerQuestion(BaseModel):
    session_key: int
    question: str = Field(min_length=3, max_length=2000)
    driver_a: int | None = None
    driver_b: int | None = None
    lap_a: int | None = None
    lap_b: int | None = None
    include_strategy: bool = True
    include_corners: bool = True


async def build_evidence(request: EngineerQuestion) -> dict:
    """Assemble everything the question could legitimately be answered from."""
    service = SessionService()
    evidence: dict = {"session_key": request.session_key}

    evidence["feed_status"] = (await service.feed_status(request.session_key)).model_dump(mode="json")

    drivers = await service.provider.drivers(request.session_key)
    evidence["drivers"] = [
        {
            "driver_number": d.driver_number,
            "acronym": d.label,
            "team": d.team_name,
        }
        for d in drivers
    ]

    if request.driver_a and request.driver_b:
        if request.include_corners:
            try:
                analysis = await service.corner_analysis(
                    request.session_key,
                    request.driver_a,
                    request.driver_b,
                    lap_a=request.lap_a,
                    lap_b=request.lap_b,
                )
                evidence["corner_analysis"] = analysis["comparison"]
                evidence["corner_method"] = analysis["method"]
                evidence["circuit"] = {
                    "lap_length_m": analysis["circuit"]["lap_length_m"],
                    "corner_count": len(analysis["circuit"]["corners"]),
                    "detection_method": analysis["circuit"]["method"],
                    "notes": analysis["circuit"]["notes"],
                }
            except (ProviderError, ValueError) as exc:
                evidence["corner_analysis_unavailable"] = str(exc)

        try:
            comparison = await service.compare(
                request.session_key,
                request.driver_a,
                request.lap_a,
                request.driver_b,
                request.lap_b,
            )
            delta = comparison["delta"]
            # Send the summary, not 500 raw points: the model should reason over
            # the computed structure, not re-derive it from arrays.
            evidence["lap_delta"] = {
                "driver_a": delta["driver_a"],
                "driver_b": delta["driver_b"],
                "lap_a": delta["lap_a"],
                "lap_b": delta["lap_b"],
                "final_delta_s": delta["final_delta_s"],
                "alignment_mode": delta["alignment_mode"],
                "spatial_resolution_m": delta["spatial_resolution_m"],
                "sectors": delta["sectors"],
                "largest_regions": sorted(
                    delta["regions"], key=lambda r: abs(r["delta_change_s"]), reverse=True
                )[:8],
                "notes": delta["notes"],
            }
        except (ProviderError, ValueError) as exc:
            evidence["lap_delta_unavailable"] = str(exc)

    if request.include_strategy:
        try:
            strategy = await service.strategy(request.session_key)
            evidence["strategy"] = {
                "stints": [
                    {
                        k: v
                        for k, v in stint.items()
                        if k != "lap_times"  # the fit summarises these
                    }
                    for stint in strategy["stints"]
                ],
                "pit_stops": strategy["pit_stops"],
                "neutralised_laps": strategy["neutralised_laps"],
                "assumptions": strategy["assumptions"],
            }
        except ProviderError as exc:
            evidence["strategy_unavailable"] = str(exc)

    return evidence


@router.post("/ask")
async def ask(request: EngineerQuestion) -> dict:
    try:
        evidence = await build_evidence(request)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status or 502, detail=str(exc)) from exc

    settings = get_settings()
    key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")

    if not key:
        return {
            "answer": None,
            "evidence": evidence,
            "narration": "unavailable",
            "reason": (
                "No ANTHROPIC_API_KEY is configured, so no narrative was generated. The "
                "evidence bundle below is the complete computed basis for an answer and is "
                "usable on its own."
            ),
        }

    try:
        import anthropic  # imported lazily so the server runs without it
    except ImportError:
        return {
            "answer": None,
            "evidence": evidence,
            "narration": "unavailable",
            "reason": "The anthropic package is not installed (pip install anthropic).",
        }

    client = anthropic.AsyncAnthropic(api_key=key)
    try:
        message = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1600,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"QUESTION:\n{request.question}\n\n"
                        f"EVIDENCE:\n{json.dumps(evidence, indent=2, default=str)}"
                    ),
                }
            ],
        )
        answer = "".join(block.text for block in message.content if block.type == "text")
    except Exception as exc:  # noqa: BLE001 - surfaced to the client verbatim
        return {
            "answer": None,
            "evidence": evidence,
            "narration": "failed",
            "reason": f"Model call failed: {exc}",
        }

    return {
        "answer": answer,
        "evidence": evidence,
        "narration": "generated",
        "model": settings.anthropic_model,
        "disclaimer": (
            "Generated from the evidence bundle returned alongside it. Every figure in the "
            "answer should be checkable against that bundle; if one is not, treat it as an error."
        ),
    }
