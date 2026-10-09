"""T1API (Turn One) adapter — registered but deliberately incomplete.

Why this is a stub rather than a provider:

T1API serves *completed analyses* (a PNG plot endpoint and the JSON behind it,
across ~48 analyses) rather than sample-level telemetry. That is a good product
for publishing race graphics, but it cannot support this application's core
requirements:

* §6 demands a configurable brake-onset threshold. You cannot re-run a
  detection algorithm against somebody else's finished chart.
* §10 demands every number be traceable to a lap, driver or calculation. A
  third-party analysis is traceable only to the fact that it was asserted.

What it *is* good for, and why the slot exists at all:

* Coverage from 2018 — five seasons further back than OpenF1's 2023 floor.
* Cross-validation. Computing our own stint timeline and diffing it against
  theirs is a genuine test of our analytics.

Freshness: the documentation describes day-long caching of finished sessions
and a 503 + Retry-After for sessions not yet published. There is no mention of
live timing, streaming transports or latency, so ``live`` is False and the UI
will not offer the live timing tower against this provider.

Licensing is unresolved: no standalone terms are published and the docs state
only that underlying timing data is subject to an unnamed provider's terms.
Until that is clarified this adapter stays disabled by default.
"""

from __future__ import annotations

from datetime import datetime

from ..models import Meeting, Session
from .base import CapabilityError, ProviderCapabilities

_NOT_SUPPORTED = (
    "T1API serves pre-computed analyses, not sample-level telemetry. "
    "Select the OpenF1 provider for this workspace."
)


class T1ApiProvider:
    """Not wired to the network. Present so the registry and UI can reason
    about it, and so finishing it later is an adapter change only."""

    def __init__(self, api_key: str | None = None, base_url: str = "https://api.t1f1.com") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="t1api",
            display_name="T1API (Turn One)",
            raw_telemetry=False,
            car_location=False,
            live=False,
            streaming=False,
            race_control=False,
            weather=False,
            stints=True,
            pit_stops=True,
            intervals=False,
            team_radio=False,
            first_season=2018,
            last_season=2030,
            requires_key=True,
            has_key=bool(self._api_key),
            attribution="T1API by Turn One. Independent project, not affiliated with "
            "Formula 1, the FIA or any team.",
            licence_note="No standalone terms or licence published at the time of writing. "
            "The documentation states underlying timing data is subject to an unnamed "
            "upstream provider's terms. Resolve this before any public deployment or "
            "redistribution of derived figures.",
        )

    @property
    def enabled(self) -> bool:
        return False

    async def meetings(self, year: int) -> list[Meeting]:
        raise CapabilityError("The T1API adapter is not implemented yet.")

    async def sessions(self, *, year: int | None = None, meeting_key: int | None = None) -> list[Session]:
        raise CapabilityError("The T1API adapter is not implemented yet.")

    async def session(self, session_key: int) -> Session | None:
        raise CapabilityError("The T1API adapter is not implemented yet.")

    async def car_data(self, *args, **kwargs):
        raise CapabilityError(_NOT_SUPPORTED)

    async def location(self, *args, **kwargs):
        raise CapabilityError(_NOT_SUPPORTED)

    async def aclose(self) -> None:
        return None
