"""Classification logic for the timing tower.

These are pure decisions about what a row means, kept out of the service layer
so they can be tested without a network or a web framework. The service does
the fetching; this module decides what the numbers are allowed to claim.

The problem this exists to solve: the provider's ``gap_to_leader`` and
``interval`` are last-known values, not live ones. When a car stops, its final
figures stay in the feed unchanged. Rendering them as current makes a car fifty
laps down read as though it is half a second behind the leader.
"""

from __future__ import annotations

RETIREMENT_LAP_MARGIN = 2
"""How many laps behind the leader still counts as circulating.

A lapped car in a long race can genuinely be two laps down and still running,
so the threshold is deliberately generous: being wrong in this direction shows
a slightly stale gap, while being wrong in the other direction would wrongly
label a running car as retired.
"""


def is_retired(laps_completed: int, leader_laps: int, *, margin: int = RETIREMENT_LAP_MARGIN) -> bool:
    """Whether a car should be treated as no longer running.

    Lap count is the only reliable signal available: the provider publishes no
    explicit retirement flag, and the position field keeps its last value too.
    """
    if leader_laps <= 0:
        return False
    return laps_completed < leader_laps - margin


def tower_sort_key(row: dict) -> tuple:
    """Order the field: classified runners first, then cars that stopped.

    Sorting everyone by the feed's position puts a car that retired on lap 8
    above one that retired on lap 37 whenever their frozen positions happen to
    fall that way. Runners keep their classified order; the rest are ordered by
    how far they actually got, which is the only meaningful thing left to say
    about them.
    """
    retired = bool(row.get("retired"))
    if retired:
        return (1, 0, -int(row.get("laps_completed") or 0))
    position = row.get("position")
    return (0, 1 if position is None else 0, position or 999)


def visible_gaps(
    *,
    retired: bool,
    position: int | None,
    gap_to_leader: float | str | None,
    interval: float | str | None,
) -> tuple[float | str | None, float | str | None]:
    """Decide which of a row's gap figures may be shown.

    Returns ``(gap_to_leader, interval)`` with anything misleading replaced by
    ``None`` so the UI renders a dash rather than a stale or meaningless number.

    Two cases are suppressed:

    * A retired car's figures are frozen at the moment it stopped.
    * The leader has no car ahead, so its interval is not zero — it does not
      exist. Showing ``0`` there renders as "LEADER" in both columns.
    """
    if retired:
        return None, None
    if position == 1:
        return gap_to_leader, None
    return gap_to_leader, interval


def last_timed_lap(laps: list):
    """The most recent lap that actually has a time.

    The final entry for a driver is frequently an in-lap, an abandoned flying
    lap, or a lap the session ended during — none of which carry a duration.
    Taking the last entry regardless is why the tower's "last lap" column sat
    empty for most of the field in qualifying.
    """
    for lap in reversed(laps):
        if getattr(lap, "lap_duration", None) is not None:
            return lap
    return None
