"""Tyre stints, degradation, and strategy scenarios.

Everything in this module is a model. The provider measures lap times, stint
boundaries and compounds; it does not measure tyre condition, fuel load or
grip. So the distinction this module enforces throughout is:

**Observed**   lap times, compound, stint lap ranges, pit-stop durations.
**Estimated**  degradation rate, fuel-corrected pace, undercut outcomes.

A degradation figure is a regression slope with a standard error, an R², and a
sample size attached — never a bare number. If a stint has four usable laps,
the slope is reported with a standard error wide enough to make clear it means
very little, instead of being hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import Lap, PitStop, Stint


@dataclass(frozen=True)
class StrategyAssumptions:
    """Every assumption behind an estimate, in one object that travels with it.

    The defaults are conventional modern-era figures, not measurements. They
    are exposed so an engineer can substitute circuit-specific values, and they
    are echoed in every result so no figure is ever read without them.
    """

    fuel_effect_s_per_lap: float = 0.055
    """How much a lap time improves per lap purely from burning fuel. Derived
    from roughly 1.8 kg burned per lap at about 0.03 s per kg. Circuit and
    car dependent."""

    pit_lane_loss_s: float = 21.0
    """Total time lost pitting versus staying out, pit lane plus the stop.
    Varies from roughly 16 s to 28 s across the calendar; override per circuit."""

    out_lap_penalty_s: float = 1.2
    """Time lost on an out-lap bringing a new set up to temperature."""

    in_lap_penalty_s: float = 0.5
    """Time lost on the lap into the pits versus a normal lap."""

    safety_car_pit_saving_s: float = 10.0
    """Portion of the pit-lane loss recovered when the field is slowed."""

    traffic_loss_s_per_lap: float = 0.0
    """Optional allowance for running in dirty air. Zero by default because
    the provider gives no direct measurement of it."""

    def as_dict(self) -> dict:
        return {
            "fuel_effect_s_per_lap": self.fuel_effect_s_per_lap,
            "pit_lane_loss_s": self.pit_lane_loss_s,
            "out_lap_penalty_s": self.out_lap_penalty_s,
            "in_lap_penalty_s": self.in_lap_penalty_s,
            "safety_car_pit_saving_s": self.safety_car_pit_saving_s,
            "traffic_loss_s_per_lap": self.traffic_loss_s_per_lap,
        }


@dataclass
class DegradationFit:
    slope_s_per_lap: float | None
    slope_stderr: float | None
    intercept_s: float | None
    r_squared: float | None
    sample_size: int
    excluded_laps: list[int] = field(default_factory=list)
    fuel_corrected: bool = False
    provenance: str = "estimated"
    notes: list[str] = field(default_factory=list)

    @property
    def is_meaningful(self) -> bool:
        """Refuse to dress up a slope that the data cannot support."""
        if self.slope_s_per_lap is None or self.slope_stderr is None:
            return False
        if self.sample_size < 5:
            return False
        return abs(self.slope_s_per_lap) > self.slope_stderr


@dataclass
class StintSummary:
    driver_number: int
    stint_number: int | None
    compound: str | None
    lap_start: int | None
    lap_end: int | None
    tyre_age_at_start: int | None
    laps_completed: int
    lap_times: list[tuple[int, float, int]] = field(default_factory=list)
    """(lap_number, lap_duration, tyre_age) for each usable lap."""
    median_s: float | None = None
    best_s: float | None = None
    degradation: DegradationFit | None = None
    notes: list[str] = field(default_factory=list)


def build_stints(
    stints: list[Stint],
    laps: list[Lap],
    *,
    excluded_lap_numbers: set[int] | None = None,
    assumptions: StrategyAssumptions | None = None,
    fuel_correct: bool = True,
) -> list[StintSummary]:
    assumptions = assumptions or StrategyAssumptions()
    excluded_lap_numbers = excluded_lap_numbers or set()

    by_driver: dict[int, dict[int, Lap]] = {}
    for lap in laps:
        if lap.lap_number is not None:
            by_driver.setdefault(lap.driver_number, {})[lap.lap_number] = lap

    summaries: list[StintSummary] = []
    for stint in stints:
        driver_laps = by_driver.get(stint.driver_number, {})
        start, end = stint.lap_start, stint.lap_end
        if start is None or end is None:
            summaries.append(
                StintSummary(
                    driver_number=stint.driver_number,
                    stint_number=stint.stint_number,
                    compound=stint.compound,
                    lap_start=start,
                    lap_end=end,
                    tyre_age_at_start=stint.tyre_age_at_start,
                    laps_completed=0,
                    notes=["stint lap range incomplete in the feed"],
                )
            )
            continue

        age0 = stint.tyre_age_at_start if stint.tyre_age_at_start is not None else 0
        points: list[tuple[int, float, int]] = []
        notes: list[str] = []

        for lap_number in range(start, end + 1):
            lap = driver_laps.get(lap_number)
            if lap is None or lap.lap_duration is None:
                continue
            if lap.is_pit_out_lap:
                notes.append(f"lap {lap_number} excluded: pit-out lap")
                continue
            if lap_number in excluded_lap_numbers:
                notes.append(f"lap {lap_number} excluded: flagged in-lap or neutralised")
                continue
            points.append((lap_number, float(lap.lap_duration), age0 + (lap_number - start)))

        times = np.array([p[1] for p in points]) if points else np.zeros(0)
        median = float(np.median(times)) if times.size else None
        best = float(times.min()) if times.size else None

        summaries.append(
            StintSummary(
                driver_number=stint.driver_number,
                stint_number=stint.stint_number,
                compound=stint.compound,
                lap_start=start,
                lap_end=end,
                tyre_age_at_start=stint.tyre_age_at_start,
                laps_completed=end - start + 1,
                lap_times=points,
                median_s=median,
                best_s=best,
                degradation=fit_degradation(points, assumptions=assumptions, fuel_correct=fuel_correct),
                notes=notes,
            )
        )
    return summaries


def fit_degradation(
    points: list[tuple[int, float, int]],
    *,
    assumptions: StrategyAssumptions | None = None,
    fuel_correct: bool = True,
    outlier_factor: float = 1.05,
) -> DegradationFit:
    """Least-squares fit of lap time against tyre age.

    Outliers above ``outlier_factor`` times the stint median are removed first:
    traffic and lock-ups produce lap times that have nothing to do with tyre
    wear and would otherwise dominate a short stint's slope.

    With ``fuel_correct`` the slope is adjusted by the assumed fuel-burn effect.
    Fuel burn makes lap times *fall* as the stint goes on, so the raw slope
    understates degradation; the corrected slope adds it back. The fuel figure
    is an assumption, which is why the result records that it was applied.
    """
    assumptions = assumptions or StrategyAssumptions()

    if len(points) < 3:
        return DegradationFit(
            slope_s_per_lap=None,
            slope_stderr=None,
            intercept_s=None,
            r_squared=None,
            sample_size=len(points),
            notes=["fewer than three usable laps — no degradation estimate attempted"],
        )

    times = np.array([p[1] for p in points], dtype=float)
    ages = np.array([p[2] for p in points], dtype=float)
    lap_numbers = [p[0] for p in points]

    median = float(np.median(times))
    keep = times <= median * outlier_factor
    excluded = [lap_numbers[i] for i in range(len(points)) if not keep[i]]

    times, ages = times[keep], ages[keep]
    notes: list[str] = []
    if excluded:
        notes.append(
            f"{len(excluded)} lap(s) above {outlier_factor:.0%} of the stint median excluded "
            f"as compromised: {excluded}"
        )

    n = times.size
    if n < 3 or np.ptp(ages) == 0:
        return DegradationFit(
            slope_s_per_lap=None,
            slope_stderr=None,
            intercept_s=None,
            r_squared=None,
            sample_size=int(n),
            excluded_laps=excluded,
            notes=notes + ["not enough spread in tyre age to fit a trend"],
        )

    slope, intercept = np.polyfit(ages, times, 1)
    predicted = slope * ages + intercept
    residuals = times - predicted
    ss_residual = float(np.sum(residuals**2))
    ss_total = float(np.sum((times - times.mean()) ** 2))
    r_squared = 1.0 - ss_residual / ss_total if ss_total > 0 else None

    # Standard error of the slope: sqrt( SSE / (n-2) / Sxx )
    sxx = float(np.sum((ages - ages.mean()) ** 2))
    stderr = float(np.sqrt((ss_residual / (n - 2)) / sxx)) if n > 2 and sxx > 0 else None

    corrected = float(slope)
    if fuel_correct:
        corrected += assumptions.fuel_effect_s_per_lap
        notes.append(
            f"fuel-corrected by +{assumptions.fuel_effect_s_per_lap:.3f} s/lap "
            f"(raw slope {slope:+.3f}); the fuel effect is assumed, not measured"
        )

    if stderr is not None and abs(corrected) <= stderr:
        notes.append(
            "the fitted slope is smaller than its own standard error — this stint shows no "
            "degradation trend that can be distinguished from scatter"
        )

    return DegradationFit(
        slope_s_per_lap=corrected,
        slope_stderr=stderr,
        intercept_s=float(intercept),
        r_squared=r_squared,
        sample_size=int(n),
        excluded_laps=excluded,
        fuel_corrected=fuel_correct,
        notes=notes,
    )


# ----------------------------------------------------------------------
# Pit stops
# ----------------------------------------------------------------------


@dataclass
class PitSummary:
    driver_number: int
    stops: int
    durations_s: list[float]
    best_s: float | None
    median_s: float | None
    note: str = (
        "Durations are the provider's stationary/pit-lane figure, not the total time lost "
        "relative to staying out on track. Compare like with like."
    )


def summarise_pit_stops(pit_stops: list[PitStop]) -> list[PitSummary]:
    grouped: dict[int, list[float]] = {}
    for stop in pit_stops:
        if stop.pit_duration is not None:
            grouped.setdefault(stop.driver_number, []).append(float(stop.pit_duration))
    out = []
    for driver, durations in sorted(grouped.items()):
        array = np.array(durations)
        out.append(
            PitSummary(
                driver_number=driver,
                stops=len(durations),
                durations_s=durations,
                best_s=float(array.min()),
                median_s=float(np.median(array)),
            )
        )
    return out


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------


@dataclass
class ScenarioTerm:
    label: str
    seconds: float
    basis: str


@dataclass
class ScenarioResult:
    name: str
    net_seconds: float
    terms: list[ScenarioTerm]
    assumptions: dict
    provenance: str = "estimated"
    caveat: str = (
        "A scenario is arithmetic over assumptions, not a prediction. It ignores "
        "traffic position, tyre warm-up variation between compounds, and the reaction "
        "of every other car on track."
    )

    @property
    def favourable(self) -> bool:
        return self.net_seconds > 0


def undercut_scenario(
    *,
    fresh_tyre_gain_s_per_lap: float,
    laps_until_rival_pits: int,
    assumptions: StrategyAssumptions | None = None,
    degradation_s_per_lap: float | None = None,
    extra_laps_on_old_tyre_at_end: int = 0,
) -> ScenarioResult:
    """Model an undercut as a sum of named, signed terms.

    Positive ``net_seconds`` means the undercut gains time. Every term is listed
    with where its number came from so the arithmetic can be audited or
    overridden, rather than arriving as a single opaque verdict.
    """
    assumptions = assumptions or StrategyAssumptions()
    terms: list[ScenarioTerm] = []

    gain = fresh_tyre_gain_s_per_lap * laps_until_rival_pits
    terms.append(
        ScenarioTerm(
            "Fresh-tyre pace advantage",
            gain,
            f"{fresh_tyre_gain_s_per_lap:.3f} s/lap advantage over {laps_until_rival_pits} lap(s), "
            "taken from the measured pace difference between compounds at these tyre ages",
        )
    )
    terms.append(
        ScenarioTerm("Out-lap warm-up", -assumptions.out_lap_penalty_s, "assumption")
    )
    terms.append(
        ScenarioTerm("In-lap compromise", -assumptions.in_lap_penalty_s, "assumption")
    )

    if assumptions.traffic_loss_s_per_lap:
        loss = -assumptions.traffic_loss_s_per_lap * laps_until_rival_pits
        terms.append(ScenarioTerm("Traffic on the out-lap phase", loss, "assumption"))

    if extra_laps_on_old_tyre_at_end and degradation_s_per_lap:
        penalty = -abs(degradation_s_per_lap) * extra_laps_on_old_tyre_at_end
        terms.append(
            ScenarioTerm(
                "Extra laps on the older set at the end",
                penalty,
                f"stopping earlier means {extra_laps_on_old_tyre_at_end} more lap(s) on a worn "
                f"set at the fitted {abs(degradation_s_per_lap):.3f} s/lap degradation",
            )
        )

    return ScenarioResult(
        name="Undercut",
        net_seconds=sum(t.seconds for t in terms),
        terms=terms,
        assumptions=assumptions.as_dict(),
    )


def safety_car_scenario(
    *,
    laps_remaining: int,
    current_tyre_age: int,
    degradation_s_per_lap: float | None,
    assumptions: StrategyAssumptions | None = None,
) -> ScenarioResult:
    """Value of pitting under a safety car versus staying out."""
    assumptions = assumptions or StrategyAssumptions()
    terms = [
        ScenarioTerm(
            "Pit-lane loss recovered while the field is slowed",
            assumptions.safety_car_pit_saving_s,
            "assumption; depends on where the safety car is relative to the pit entry",
        )
    ]
    if degradation_s_per_lap:
        saved = abs(degradation_s_per_lap) * laps_remaining
        terms.append(
            ScenarioTerm(
                "Degradation avoided over the remaining distance",
                saved,
                f"{laps_remaining} lap(s) at the fitted {abs(degradation_s_per_lap):.3f} s/lap, "
                f"from a set currently {current_tyre_age} lap(s) old",
            )
        )
    else:
        terms.append(
            ScenarioTerm(
                "Degradation avoided",
                0.0,
                "no usable degradation fit for this stint — this term cannot be estimated",
            )
        )
    terms.append(ScenarioTerm("Out-lap warm-up", -assumptions.out_lap_penalty_s, "assumption"))

    return ScenarioResult(
        name="Pit under safety car",
        net_seconds=sum(t.seconds for t in terms),
        terms=terms,
        assumptions=assumptions.as_dict(),
    )
