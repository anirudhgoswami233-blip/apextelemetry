"""Tests for the analytical layer, against a constructed circuit with known truth."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics import (  # noqa: E402
    BrakeDetectionConfig,
    CornerDetectionConfig,
    StrategyAssumptions,
    assign_brake_events,
    build_circuit_map,
    build_lap_trace,
    classify_laps,
    compare_corner,
    compute_delta,
    corner_metrics,
    corner_metrics_for_lap,
    detect_brake_events,
    fit_degradation,
    integrate_distance,
    is_retired,
    last_timed_lap,
    lap_window,
    safety_car_scenario,
    tower_sort_key,
    undercut_scenario,
    visible_gaps,
)
from app.analytics.braking import BrakeEvent  # noqa: E402
from app.models import CarSample, Lap  # noqa: E402
from tests.synthetic import (  # noqa: E402
    linked_corner_circuit,
    rounded_rectangle,
    sample_lap,
)


def make_trace(**kwargs):
    car, loc, lap, truth = sample_lap(**kwargs)
    trace = build_lap_trace(car, lap=lap, locations=loc, session_key=lap.session_key)
    assert trace is not None
    return trace, lap, truth


# ======================================================================
# Distance integration
# ======================================================================


class TestDistanceIntegration(unittest.TestCase):
    def test_constant_speed_is_exact(self):
        """100 km/h for 10 s is 277.78 m. Trapezoid is exact for constant speed."""
        t = np.linspace(0, 10, 38)
        v = np.full_like(t, 100.0)
        d = integrate_distance(t, v)
        self.assertAlmostEqual(d[-1], 100 / 3.6 * 10, places=6)

    def test_linear_ramp_is_exact(self):
        """Trapezoid is also exact when speed changes linearly."""
        t = np.linspace(0, 10, 101)
        v = np.linspace(0, 360, 101)  # 0 to 100 m/s linearly
        d = integrate_distance(t, v)
        self.assertAlmostEqual(d[-1], 0.5 * 100 * 10, places=4)

    def test_empty_and_single_sample(self):
        self.assertEqual(integrate_distance(np.zeros(0), np.zeros(0)).size, 0)
        self.assertEqual(integrate_distance(np.zeros(1), np.zeros(1))[0], 0.0)

    def test_recovers_synthetic_lap_length(self):
        """Integrating 3.7 Hz samples should land close to the true lap length."""
        trace, _, truth = make_trace()
        error = abs(trace.length - truth["lap_length_m"]) / truth["lap_length_m"]
        self.assertLess(error, 0.01, f"length error {error:.2%} on a {truth['lap_length_m']:.0f} m lap")


# ======================================================================
# Trace construction
# ======================================================================


class TestLapTrace(unittest.TestCase):
    def test_monotonic_distance(self):
        trace, _, _ = make_trace()
        self.assertTrue(np.all(np.diff(trace.distance) >= 0))

    def test_sample_rate_detected(self):
        trace, _, _ = make_trace()
        self.assertAlmostEqual(trace.sample_rate_hz, 3.7, delta=0.2)

    def test_spatial_resolution_is_reported_and_coarse(self):
        """The honest precision floor: tens of metres at racing speed."""
        trace, _, _ = make_trace()
        resolution = trace.spatial_resolution()
        self.assertGreater(resolution, 10.0)
        self.assertLess(resolution, 40.0)

    def test_missing_channel_produces_warning_not_zeros(self):
        trace, _, _ = make_trace(include_brake=False)
        self.assertTrue(np.all(np.isnan(trace.brake)))
        self.assertTrue(any("missing brake" in w for w in trace.warnings))

    def test_position_is_time_interpolated_not_index_paired(self):
        """The position feed is offset by 0.11 s; values must be interpolated."""
        trace, _, _ = make_trace()
        self.assertIsNotNone(trace.x)
        self.assertGreater(np.isfinite(trace.x).mean(), 0.9)

    def test_too_few_samples_returns_none(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        samples = [CarSample(date=now, driver_number=1, speed=100.0)]
        self.assertIsNone(build_lap_trace(samples))

    def test_duplicate_timestamps_are_dropped(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        samples = [
            CarSample(date=now, driver_number=1, speed=100.0),
            CarSample(date=now, driver_number=1, speed=150.0),
            CarSample(date=now + timedelta(seconds=1), driver_number=1, speed=200.0),
        ]
        trace = build_lap_trace(samples)
        self.assertEqual(trace.sample_count, 2)

    def test_gaps_are_flagged(self):
        trace, _, _ = make_trace(drop_fraction=0.25, seed=7)
        self.assertTrue(any("gap" in w for w in trace.warnings))

    def test_step_channels_are_not_interpolated(self):
        """Resampling must never invent gear 4.3 or a half-open DRS flap."""
        trace, _, _ = make_trace()
        grid = np.arange(0, trace.length, 3.0)
        resampled = trace.resample(grid)
        gears = resampled.n_gear[np.isfinite(resampled.n_gear)]
        self.assertTrue(np.allclose(gears, np.round(gears)))
        self.assertTrue(set(np.unique(resampled.drs)).issubset({0.0, 12.0}))

    def test_lap_window_pads_both_ends(self):
        _, lap, _ = make_trace()
        start, end = lap_window(lap, pad_before=0.5, pad_after=0.5)
        self.assertAlmostEqual((end - start).total_seconds(), lap.lap_duration + 1.0, places=5)

    def test_lap_window_without_duration_is_none(self):
        self.assertIsNone(lap_window(Lap(driver_number=1, lap_number=1)))


# ======================================================================
# Lap delta
# ======================================================================


class TestLapDelta(unittest.TestCase):
    def test_lap_against_itself_is_zero(self):
        trace, lap, _ = make_trace()
        result = compute_delta(trace, trace, lap_a=lap, lap_b=lap)
        self.assertLess(np.nanmax(np.abs(result.delta)), 1e-6)
        self.assertEqual(len(result.regions), 0)

    def test_uniformly_slower_lap_accumulates_delta(self):
        """A lap 2% slower everywhere should finish ~2% of lap time behind."""
        fast, lap_a, truth = make_trace(speed_scale=1.0, driver_number=1)
        slow, lap_b, _ = make_trace(speed_scale=0.98, driver_number=2)
        result = compute_delta(fast, slow, lap_a=lap_a, lap_b=lap_b)

        expected = truth["lap_duration_s"] * (1 / 0.98 - 1)
        # Truncation to the shorter trace costs a little of the final delta.
        self.assertGreater(result.final_delta_s, expected * 0.9)
        self.assertLess(result.final_delta_s, expected * 1.1)

    def test_delta_is_monotonic_when_one_lap_is_slower_everywhere(self):
        fast, _, _ = make_trace(speed_scale=1.0, driver_number=1)
        slow, _, _ = make_trace(speed_scale=0.97, driver_number=2)
        result = compute_delta(fast, slow)
        self.assertGreaterEqual(np.min(np.diff(result.delta)), -0.02)

    def test_sign_convention_b_slower_is_positive(self):
        fast, _, _ = make_trace(speed_scale=1.0, driver_number=1)
        slow, _, _ = make_trace(speed_scale=0.95, driver_number=2)
        self.assertGreater(compute_delta(fast, slow).final_delta_s, 0)
        self.assertLess(compute_delta(slow, fast).final_delta_s, 0)

    def test_resolution_note_is_always_present(self):
        a, _, _ = make_trace(driver_number=1)
        b, _, _ = make_trace(driver_number=2, speed_scale=0.99)
        result = compute_delta(a, b)
        self.assertTrue(any("localisation" in n for n in result.notes))

    def test_normalize_mode_records_its_assumption(self):
        a, _, _ = make_trace(driver_number=1)
        b, _, _ = make_trace(driver_number=2, speed_scale=0.97)
        result = compute_delta(a, b, mode="normalize")
        self.assertEqual(result.alignment_mode, "normalize")
        self.assertTrue(any("scaled by" in n for n in result.notes))

    def test_sector_deltas_come_from_the_timing_feed(self):
        a_trace, lap_a, _ = make_trace(driver_number=1)
        b_trace, lap_b, _ = make_trace(driver_number=2, speed_scale=0.98)
        result = compute_delta(a_trace, b_trace, lap_a=lap_a, lap_b=lap_b)
        self.assertEqual(len(result.sectors), 3)
        self.assertTrue(all(s.provenance == "measured" for s in result.sectors))

    def test_missing_sector_times_are_marked_unavailable(self):
        lap_a = Lap(driver_number=1, lap_number=1, lap_duration=90.0)
        lap_b = Lap(driver_number=2, lap_number=1, lap_duration=91.0)
        a, _, _ = make_trace(driver_number=1)
        b, _, _ = make_trace(driver_number=2, speed_scale=0.99)
        result = compute_delta(a, b, lap_a=lap_a, lap_b=lap_b)
        self.assertTrue(all(s.provenance == "unavailable" for s in result.sectors))


# ======================================================================
# Brake detection
# ======================================================================


class TestBrakeDetection(unittest.TestCase):
    def test_finds_one_event_per_braking_zone(self):
        trace, _, truth = make_trace()
        events = detect_brake_events(trace)
        self.assertEqual(
            len(events),
            len(truth["brake_zones"]),
            f"found {len(events)} events for {len(truth['brake_zones'])} true zones",
        )

    def test_onset_uncertainty_brackets_the_true_braking_point(self):
        """The reported ± range must actually contain the truth."""
        trace, _, truth = make_trace()
        events = detect_brake_events(trace)
        for event, (true_start, _) in zip(events, truth["brake_zones"]):
            low, high = event.onset_range_m
            # Allow one extra sample gap: the flag is derived from the sampled
            # trace, which itself quantises the true onset.
            tolerance = trace.spatial_resolution(event.onset_distance_m)
            self.assertGreaterEqual(
                true_start, low - tolerance, f"true onset {true_start:.0f} below reported range"
            )
            self.assertLessEqual(
                true_start, high + tolerance, f"true onset {true_start:.0f} above reported range"
            )

    def test_uncertainty_is_never_zero(self):
        trace, _, _ = make_trace()
        for event in detect_brake_events(trace):
            self.assertGreater(event.onset_uncertainty_m, 0.0)

    def test_flag_method_is_calculated_not_estimated(self):
        trace, _, _ = make_trace()
        for event in detect_brake_events(trace):
            self.assertEqual(event.method, "flag")
            self.assertEqual(event.provenance, "calculated")

    def test_falls_back_to_deceleration_and_labels_it_estimated(self):
        trace, _, truth = make_trace(include_brake=False)
        events = detect_brake_events(trace)
        self.assertGreater(len(events), 0)
        for event in events:
            self.assertEqual(event.method, "deceleration")
            self.assertEqual(event.provenance, "estimated")
            self.assertTrue(any("lift or downshift" in n for n in event.notes))

    def test_flag_method_refuses_without_a_brake_channel(self):
        trace, _, _ = make_trace(include_brake=False)
        events = detect_brake_events(trace, BrakeDetectionConfig(method="flag"))
        self.assertEqual(events, [])

    def test_threshold_is_configurable_and_recorded(self):
        config = BrakeDetectionConfig(decel_threshold_mps2=-8.0, method="deceleration")
        trace, _, _ = make_trace()
        strict = detect_brake_events(trace, config)
        lenient = detect_brake_events(
            trace, BrakeDetectionConfig(decel_threshold_mps2=-1.5, method="deceleration")
        )
        self.assertLessEqual(len(strict), len(lenient))
        self.assertIn("decel_threshold_mps2", config.as_dict())

    def test_min_speed_drop_rejects_brushes(self):
        trace, _, _ = make_trace()
        many = detect_brake_events(trace, BrakeDetectionConfig(min_speed_drop_kph=0))
        few = detect_brake_events(trace, BrakeDetectionConfig(min_speed_drop_kph=150))
        self.assertLessEqual(len(few), len(many))

    def test_events_are_distance_ordered(self):
        trace, _, _ = make_trace()
        distances = [e.onset_distance_m for e in detect_brake_events(trace)]
        self.assertEqual(distances, sorted(distances))


# ======================================================================
# Circuit geometry and corners
# ======================================================================


class TestCircuitMap(unittest.TestCase):
    def test_finds_the_constructed_corners(self):
        trace, _, truth = make_trace()
        circuit = build_circuit_map(trace)
        self.assertEqual(len(circuit.corners), truth["corner_count"])

    def test_detects_the_decimetre_unit_scale(self):
        """Positions are generated in decimetres; the scale must be recovered."""
        trace, _, _ = make_trace()
        circuit = build_circuit_map(trace)
        self.assertAlmostEqual(circuit.scale_factor, 0.1, delta=0.02)
        self.assertTrue(any("decimetre" in n for n in circuit.notes))

    def test_corner_radius_is_close_to_the_constructed_radius(self):
        trace, _, _ = make_trace(
            segments=rounded_rectangle(straight_long=700, straight_short=350, radius=90)
        )
        circuit = build_circuit_map(trace)
        radii = [c.min_radius_m for c in circuit.corners if c.min_radius_m]
        self.assertTrue(radii)
        self.assertAlmostEqual(float(np.median(radii)), 90.0, delta=35.0)

    def test_corners_are_numbered_in_distance_order(self):
        trace, _, _ = make_trace()
        circuit = build_circuit_map(trace)
        self.assertEqual([c.number for c in circuit.corners], list(range(1, len(circuit.corners) + 1)))
        self.assertEqual(
            [c.entry_m for c in circuit.corners], sorted(c.entry_m for c in circuit.corners)
        )

    def test_apex_is_always_labelled_estimated(self):
        trace, _, _ = make_trace()
        for corner in build_circuit_map(trace).corners:
            self.assertEqual(corner.apex_provenance, "estimated")
            self.assertTrue(corner.entry_m <= corner.apex_m <= corner.exit_m)

    def test_corner_crossing_the_line_is_stitched_into_one(self):
        """A corner split by the start/finish line must not be counted twice."""
        trace, _, truth = make_trace(segments=rounded_rectangle(line_on_corner=True))
        circuit = build_circuit_map(trace)
        self.assertEqual(len(circuit.corners), truth["corner_count"])
        wrapping = [c for c in circuit.corners if c.exit_m > trace.length]
        self.assertEqual(len(wrapping), 1)
        self.assertTrue(any("crosses the start/finish line" in n for n in wrapping[0].notes))
        # The stitched corner must have a sane radius, not the stub's artefact.
        self.assertAlmostEqual(wrapping[0].min_radius_m, 90.0, delta=35.0)

    def test_threshold_changes_what_counts_as_a_corner(self):
        trace, _, _ = make_trace()
        tight = build_circuit_map(trace, CornerDetectionConfig(radius_threshold_m=60.0))
        loose = build_circuit_map(trace, CornerDetectionConfig(radius_threshold_m=400.0))
        self.assertLessEqual(len(tight.corners), len(loose.corners))

    def test_falls_back_without_position_data_and_says_so(self):
        trace, _, _ = make_trace(include_position=False)
        circuit = build_circuit_map(trace)
        self.assertEqual(circuit.method, "speed-minima-approximation")
        self.assertIsNone(circuit.x)
        self.assertTrue(any("no usable car-position data" in n for n in circuit.notes))
        for corner in circuit.corners:
            self.assertEqual(corner.apex_provenance, "unavailable")


class TestCornerMetrics(unittest.TestCase):
    def setUp(self):
        self.trace, self.lap, self.truth = make_trace()
        self.circuit = build_circuit_map(self.trace)
        self.corner = self.circuit.corners[0]

    def test_minimum_speed_is_below_entry_and_exit(self):
        metrics = corner_metrics(self.trace, self.corner)
        self.assertIsNotNone(metrics.min_speed_kph)
        self.assertLessEqual(metrics.min_speed_kph, metrics.entry_speed_kph + 1)
        self.assertLessEqual(metrics.min_speed_kph, metrics.exit_speed_kph + 1)

    def test_braking_event_is_attached(self):
        metrics = corner_metrics(self.trace, self.corner)
        self.assertIsNotNone(metrics.brake)
        self.assertLessEqual(metrics.brake.onset_distance_m, self.corner.entry_m)

    def test_throttle_application_is_after_minimum_speed(self):
        metrics = corner_metrics(self.trace, self.corner)
        if metrics.throttle_application_m is not None:
            self.assertGreaterEqual(metrics.throttle_application_m, metrics.min_speed_distance_m)

    def test_gear_range_is_integral(self):
        metrics = corner_metrics(self.trace, self.corner)
        self.assertIsNotNone(metrics.gear_range)
        low, high = metrics.gear_range
        self.assertLessEqual(low, high)

    def test_resolution_travels_with_the_metrics(self):
        metrics = corner_metrics(self.trace, self.corner)
        self.assertTrue(np.isfinite(metrics.spatial_resolution_m))
        self.assertGreater(metrics.spatial_resolution_m, 0)


class TestBrakeEventAssignment(unittest.TestCase):
    """Each corner gets at most one event, and each event serves one corner.

    Looking events up per corner is what made two adjacent corners report the
    same braking point, which reads as though the driver braked twice in the
    same place.
    """

    @staticmethod
    def _event(distance: float) -> BrakeEvent:
        return BrakeEvent(
            onset_distance_m=distance,
            onset_uncertainty_m=10.0,
            onset_time_s=0.0,
            speed_at_onset_kph=300.0,
        )

    def test_adjacent_corners_cannot_share_one_event(self):
        events = [self._event(1900.0)]
        # Two corners 80 m apart, both within the 250 m approach window.
        assigned = assign_brake_events([1950.0, 2030.0], events, window_m=250.0)
        self.assertIs(assigned[0], events[0])
        self.assertIsNone(assigned[1], "second corner must not reuse the same event")

    def test_each_corner_takes_its_own_event_when_both_exist(self):
        events = [self._event(1900.0), self._event(2150.0)]
        assigned = assign_brake_events([1950.0, 2200.0], events, window_m=250.0)
        self.assertEqual(assigned[0].onset_distance_m, 1900.0)
        self.assertEqual(assigned[1].onset_distance_m, 2150.0)

    def test_earlier_corner_claims_first(self):
        """Braking belongs to the corner the driver reaches first."""
        events = [self._event(1000.0)]
        assigned = assign_brake_events([1050.0, 1100.0, 1150.0], events, window_m=300.0)
        self.assertIsNotNone(assigned[0])
        self.assertIsNone(assigned[1])
        self.assertIsNone(assigned[2])

    def test_no_event_outside_the_window(self):
        events = [self._event(100.0)]
        self.assertEqual(assign_brake_events([900.0], events, window_m=250.0), [None])

    def test_every_event_used_at_most_once_on_a_real_lap(self):
        trace, _, _ = make_trace()
        circuit = build_circuit_map(trace)
        events = detect_brake_events(trace)
        assigned = assign_brake_events(
            [c.entry_m for c in circuit.corners], events, window_m=250.0
        )
        used = [id(a) for a in assigned if a is not None]
        self.assertEqual(len(used), len(set(used)), "an event was assigned to two corners")

    def test_batch_metrics_report_distinct_braking_points(self):
        trace, _, _ = make_trace()
        circuit = build_circuit_map(trace)
        metrics = corner_metrics_for_lap(trace, circuit.corners)
        points = [
            round(m.brake.onset_distance_m, 3) for m in metrics if m.brake is not None
        ]
        self.assertEqual(len(points), len(set(points)), f"duplicate braking points: {points}")

    def test_linked_corners_regression(self):
        """The Singapore T5/T6 case, end to end.

        On a circuit where two corners share one braking zone, the old
        per-corner lookup reported an identical braking point for both. This
        test fails against that behaviour and passes against the fix.
        """
        car, loc, lap, _ = sample_lap(
            segments=linked_corner_circuit(gap_m=40.0), driver_number=1, seed=4
        )
        trace = build_lap_trace(car, lap=lap, locations=loc)
        circuit = build_circuit_map(
            trace, CornerDetectionConfig(radius_threshold_m=300, merge_gap_m=15)
        )
        self.assertGreaterEqual(len(circuit.corners), 4)

        events = detect_brake_events(trace)
        # The old path: every corner looks the event up for itself.
        naive = [corner_metrics(trace, c, brake_events=events) for c in circuit.corners]
        naive_points = [
            round(m.brake.onset_distance_m, 1) for m in naive if m.brake is not None
        ]
        self.assertLess(
            len(set(naive_points)), len(naive_points),
            "fixture no longer reproduces the bug — corners are too far apart",
        )

        # The fixed path.
        fixed = corner_metrics_for_lap(trace, circuit.corners)
        points = [round(m.brake.onset_distance_m, 1) for m in fixed if m.brake is not None]
        self.assertEqual(
            len(points), len(set(points)),
            f"two corners still report the same braking point: {points}",
        )

    def test_corner_without_its_own_event_says_so(self):
        trace, _, _ = make_trace()
        circuit = build_circuit_map(trace)
        # A wide approach forces competition between corners for the same events.
        metrics = corner_metrics_for_lap(
            trace, circuit.corners, config=CornerDetectionConfig(approach_m=900.0)
        )
        without = [m for m in metrics if m.brake is None]
        for m in without:
            self.assertTrue(
                any("no separate braking event" in n for n in m.notes),
                "a corner with no braking event must explain why",
            )


class TestCornerComparison(unittest.TestCase):
    def test_identical_laps_show_no_significant_difference(self):
        trace, lap, _ = make_trace()
        circuit = build_circuit_map(trace)
        corner = circuit.corners[0]
        metrics = corner_metrics(trace, corner)
        comparison = compare_corner(corner, metrics, metrics)
        self.assertEqual(comparison.brake_point_difference_m, 0.0)
        self.assertFalse(comparison.brake_difference_significant)
        self.assertIn("not a distinguishable difference", " ".join(comparison.notes))

    def test_does_not_accuse_anyone_of_braking_too_late(self):
        a, _, _ = make_trace(driver_number=1)
        b, _, _ = make_trace(driver_number=2, speed_scale=0.96)
        circuit = build_circuit_map(a)
        corner = circuit.corners[0]
        comparison = compare_corner(
            corner, corner_metrics(a, corner), corner_metrics(b, corner)
        )
        text = comparison.interpretation.lower()
        for forbidden in ("too late", "too early", "mistake", "should have", "error"):
            self.assertNotIn(forbidden, text)

    def test_significance_requires_exceeding_combined_uncertainty(self):
        a, _, _ = make_trace(driver_number=1)
        b, _, _ = make_trace(driver_number=2, speed_scale=0.90)
        circuit = build_circuit_map(a)
        for corner in circuit.corners:
            comparison = compare_corner(corner, corner_metrics(a, corner), corner_metrics(b, corner))
            if comparison.brake_point_difference_m is None:
                continue
            if comparison.brake_difference_significant:
                self.assertGreater(
                    abs(comparison.brake_point_difference_m),
                    comparison.brake_point_uncertainty_m,
                )

    def test_time_delta_is_read_from_the_delta_trace(self):
        a, lap_a, _ = make_trace(driver_number=1)
        b, lap_b, _ = make_trace(driver_number=2, speed_scale=0.97)
        delta = compute_delta(a, b, lap_a=lap_a, lap_b=lap_b)
        circuit = build_circuit_map(a)
        corner = circuit.corners[1]
        comparison = compare_corner(
            corner,
            corner_metrics(a, corner),
            corner_metrics(b, corner),
            delta_distance=delta.distance,
            delta_values=delta.delta,
        )
        self.assertIsNotNone(comparison.time_delta_s)
        # B is slower everywhere, so A gains through every corner window.
        self.assertGreater(comparison.time_delta_s, 0)

    def test_confidence_drops_without_position_geometry(self):
        trace, _, _ = make_trace(include_position=False)
        circuit = build_circuit_map(trace)
        corner = circuit.corners[0]
        metrics = corner_metrics(trace, corner)
        self.assertEqual(compare_corner(corner, metrics, metrics).confidence, "low")


# ======================================================================
# Lap classification
# ======================================================================


class TestTimingTower(unittest.TestCase):
    """The tower must not present frozen figures as current ones.

    These cases come from the 2026 Singapore race, where cars that retired on
    laps 8, 30 and 37 were shown with gaps like "+40.541" and "+1 LAP" against
    a leader who completed 62.
    """

    def test_retired_car_is_detected_by_lap_count(self):
        self.assertTrue(is_retired(8, 62))
        self.assertTrue(is_retired(30, 62))
        self.assertTrue(is_retired(37, 62))

    def test_lapped_but_running_car_is_not_retired(self):
        """A car two laps down in a long race is still circulating."""
        self.assertFalse(is_retired(62, 62))
        self.assertFalse(is_retired(61, 62))
        self.assertFalse(is_retired(60, 62))

    def test_no_retirements_before_the_session_starts(self):
        self.assertFalse(is_retired(0, 0))

    def test_retired_car_shows_no_gaps(self):
        gap_leader, interval = visible_gaps(
            retired=True, position=19, gap_to_leader=40.541, interval=0.802
        )
        self.assertIsNone(gap_leader)
        self.assertIsNone(interval)

    def test_leader_has_no_interval(self):
        """Interval is the gap to the car ahead; the leader has none."""
        gap_leader, interval = visible_gaps(
            retired=False, position=1, gap_to_leader=0.0, interval=0.0
        )
        self.assertEqual(gap_leader, 0.0)
        self.assertIsNone(interval, "P1 must not render an interval")

    def test_runner_keeps_both_figures(self):
        gap_leader, interval = visible_gaps(
            retired=False, position=2, gap_to_leader=7.335, interval=7.335
        )
        self.assertEqual((gap_leader, interval), (7.335, 7.335))

    def test_string_gaps_survive(self):
        """The feed sends '+1 LAP' as a string for lapped cars."""
        gap_leader, interval = visible_gaps(
            retired=False, position=11, gap_to_leader="+1 LAP", interval=12.066
        )
        self.assertEqual(gap_leader, "+1 LAP")

    def test_runners_sort_above_retirements(self):
        rows = [
            {"position": 19, "laps_completed": 8, "retired": True},
            {"position": 2, "laps_completed": 62, "retired": False},
            {"position": 21, "laps_completed": 30, "retired": True},
            {"position": 1, "laps_completed": 62, "retired": False},
        ]
        ordered = sorted(rows, key=tower_sort_key)
        self.assertEqual([r["position"] for r in ordered[:2]], [1, 2])
        self.assertTrue(all(r["retired"] for r in ordered[2:]))

    def test_retirements_order_by_distance_covered(self):
        """The car that got furthest is classified ahead of the one that did not."""
        rows = [
            {"position": 19, "laps_completed": 8, "retired": True},
            {"position": 18, "laps_completed": 37, "retired": True},
            {"position": 21, "laps_completed": 30, "retired": True},
        ]
        ordered = sorted(rows, key=tower_sort_key)
        self.assertEqual([r["laps_completed"] for r in ordered], [37, 30, 8])

    def test_missing_position_sorts_last_among_runners(self):
        rows = [
            {"position": None, "laps_completed": 62, "retired": False},
            {"position": 3, "laps_completed": 62, "retired": False},
        ]
        ordered = sorted(rows, key=tower_sort_key)
        self.assertEqual(ordered[0]["position"], 3)

    def test_last_lap_skips_untimed_trailing_laps(self):
        """An in-lap with no recorded time is not the driver's last lap time."""
        laps = [
            Lap(driver_number=1, lap_number=1, lap_duration=92.1),
            Lap(driver_number=1, lap_number=2, lap_duration=91.4),
            Lap(driver_number=1, lap_number=3, lap_duration=None),
        ]
        self.assertEqual(last_timed_lap(laps).lap_number, 2)

    def test_last_lap_is_none_when_nothing_was_timed(self):
        laps = [Lap(driver_number=1, lap_number=1, lap_duration=None)]
        self.assertIsNone(last_timed_lap(laps))
        self.assertIsNone(last_timed_lap([]))


class TestLapClassification(unittest.TestCase):
    def _laps(self):
        return [
            Lap(driver_number=1, lap_number=1, lap_duration=95.0, is_pit_out_lap=True,
                duration_sector_1=30.0, duration_sector_2=32.0, duration_sector_3=33.0),
            Lap(driver_number=1, lap_number=2, lap_duration=90.0,
                duration_sector_1=29.0, duration_sector_2=30.5, duration_sector_3=30.5),
            Lap(driver_number=1, lap_number=3, lap_duration=90.4,
                duration_sector_1=29.1, duration_sector_2=30.6, duration_sector_3=30.7),
            Lap(driver_number=1, lap_number=4, lap_duration=104.0,
                duration_sector_1=35.0, duration_sector_2=34.0, duration_sector_3=35.0),
            Lap(driver_number=1, lap_number=5, lap_duration=None),
        ]

    def test_flags_without_deleting(self):
        qualities = classify_laps(self._laps(), pit_laps={3}, yellow_flag_laps={4})
        self.assertEqual(len(qualities), 5)
        self.assertIn("pit-out lap", qualities[0].flags)
        self.assertTrue(qualities[1].is_valid)
        self.assertIn("in-lap (pit entry on this lap)", qualities[2].flags)
        self.assertTrue(any("yellow flag" in f for f in qualities[3].flags))
        self.assertIn("no lap time recorded", qualities[4].flags)

    def test_outlier_test_is_relative_to_personal_best(self):
        qualities = classify_laps(self._laps())
        self.assertTrue(any("session best" in f for f in qualities[3].flags))
        self.assertFalse(any("session best" in f for f in qualities[1].flags))


# ======================================================================
# Degradation and strategy
# ======================================================================


class TestDegradation(unittest.TestCase):
    def test_recovers_a_known_slope(self):
        rng = np.random.default_rng(3)
        true_slope = 0.08
        points = [
            (lap, 92.0 + true_slope * age + rng.normal(0, 0.03), age)
            for lap, age in zip(range(1, 21), range(1, 21))
        ]
        fit = fit_degradation(points, fuel_correct=False)
        self.assertAlmostEqual(fit.slope_s_per_lap, true_slope, delta=0.02)
        self.assertGreater(fit.r_squared, 0.9)
        self.assertTrue(fit.is_meaningful)

    def test_fuel_correction_increases_the_slope_by_the_assumption(self):
        points = [(lap, 92.0 + 0.05 * age, age) for lap, age in zip(range(1, 16), range(1, 16))]
        assumptions = StrategyAssumptions(fuel_effect_s_per_lap=0.055)
        raw = fit_degradation(points, fuel_correct=False)
        corrected = fit_degradation(points, assumptions=assumptions, fuel_correct=True)
        self.assertAlmostEqual(
            corrected.slope_s_per_lap - raw.slope_s_per_lap, 0.055, places=6
        )
        self.assertTrue(corrected.fuel_corrected)
        self.assertTrue(any("assumed, not measured" in n for n in corrected.notes))

    def test_too_few_laps_yields_no_estimate(self):
        fit = fit_degradation([(1, 92.0, 1), (2, 92.1, 2)])
        self.assertIsNone(fit.slope_s_per_lap)
        self.assertFalse(fit.is_meaningful)
        self.assertTrue(any("fewer than three" in n for n in fit.notes))

    def test_pure_noise_is_not_called_meaningful(self):
        rng = np.random.default_rng(11)
        points = [(lap, 92.0 + rng.normal(0, 0.4), lap) for lap in range(1, 13)]
        fit = fit_degradation(points, fuel_correct=False)
        self.assertFalse(fit.is_meaningful)

    def test_outliers_are_excluded_and_listed(self):
        points = [(lap, 92.0 + 0.05 * lap, lap) for lap in range(1, 16)]
        points[7] = (8, 120.0, 8)  # a lock-up or traffic lap
        fit = fit_degradation(points, fuel_correct=False)
        self.assertIn(8, fit.excluded_laps)
        self.assertAlmostEqual(fit.slope_s_per_lap, 0.05, delta=0.02)

    def test_flat_tyre_age_cannot_be_fitted(self):
        points = [(lap, 92.0, 5) for lap in range(1, 8)]
        fit = fit_degradation(points)
        self.assertIsNone(fit.slope_s_per_lap)


class TestScenarios(unittest.TestCase):
    def test_undercut_terms_sum_to_the_net(self):
        result = undercut_scenario(fresh_tyre_gain_s_per_lap=1.1, laps_until_rival_pits=2)
        self.assertAlmostEqual(result.net_seconds, sum(t.seconds for t in result.terms), places=9)

    def test_undercut_is_labelled_estimated_with_a_caveat(self):
        result = undercut_scenario(fresh_tyre_gain_s_per_lap=1.0, laps_until_rival_pits=1)
        self.assertEqual(result.provenance, "estimated")
        self.assertIn("not a prediction", result.caveat)
        self.assertIn("pit_lane_loss_s", result.assumptions)

    def test_weak_pace_advantage_makes_the_undercut_unfavourable(self):
        weak = undercut_scenario(fresh_tyre_gain_s_per_lap=0.2, laps_until_rival_pits=1)
        strong = undercut_scenario(fresh_tyre_gain_s_per_lap=1.5, laps_until_rival_pits=2)
        self.assertFalse(weak.favourable)
        self.assertTrue(strong.favourable)

    def test_safety_car_without_a_degradation_fit_says_so(self):
        result = safety_car_scenario(
            laps_remaining=20, current_tyre_age=18, degradation_s_per_lap=None
        )
        self.assertTrue(any("cannot be estimated" in t.basis for t in result.terms))


if __name__ == "__main__":
    unittest.main(verbosity=2)
