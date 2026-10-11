"""Pure analytical layer.

Nothing here imports a web framework or an HTTP client. Every function takes
provider models or numpy arrays and returns dataclasses, which is what makes
this layer testable without a network and reusable behind any transport.
"""

from .alignment import (
    AlignedPair,
    AlignmentMode,
    LapTrace,
    align,
    build_lap_trace,
    integrate_distance,
    lap_window,
)
from .braking import (
    BrakeDetectionConfig,
    BrakeEvent,
    assign_brake_events,
    brake_event_before,
    detect_brake_events,
)
from .corners import (
    CircuitMap,
    Corner,
    CornerComparison,
    CornerDetectionConfig,
    CornerMetrics,
    build_circuit_map,
    compare_corner,
    corner_metrics,
    corner_metrics_for_lap,
)
from .delta import (
    DeltaRegion,
    LapDelta,
    LapQuality,
    SectorDelta,
    classify_laps,
    compute_delta,
    representative_laps,
    sector_delta,
)
from .timing import (
    RETIREMENT_LAP_MARGIN,
    is_retired,
    last_timed_lap,
    tower_sort_key,
    visible_gaps,
)
from .stints import (
    DegradationFit,
    PitSummary,
    ScenarioResult,
    ScenarioTerm,
    StintSummary,
    StrategyAssumptions,
    build_stints,
    fit_degradation,
    safety_car_scenario,
    summarise_pit_stops,
    undercut_scenario,
)

__all__ = [
    "AlignedPair",
    "AlignmentMode",
    "LapTrace",
    "align",
    "build_lap_trace",
    "integrate_distance",
    "lap_window",
    "BrakeDetectionConfig",
    "BrakeEvent",
    "assign_brake_events",
    "brake_event_before",
    "detect_brake_events",
    "CircuitMap",
    "Corner",
    "CornerComparison",
    "CornerDetectionConfig",
    "CornerMetrics",
    "build_circuit_map",
    "compare_corner",
    "corner_metrics",
    "corner_metrics_for_lap",
    "DeltaRegion",
    "LapDelta",
    "LapQuality",
    "SectorDelta",
    "classify_laps",
    "compute_delta",
    "representative_laps",
    "sector_delta",
    "RETIREMENT_LAP_MARGIN",
    "is_retired",
    "last_timed_lap",
    "tower_sort_key",
    "visible_gaps",
    "DegradationFit",
    "PitSummary",
    "ScenarioResult",
    "ScenarioTerm",
    "StintSummary",
    "StrategyAssumptions",
    "build_stints",
    "fit_degradation",
    "safety_car_scenario",
    "summarise_pit_stops",
    "undercut_scenario",
]
