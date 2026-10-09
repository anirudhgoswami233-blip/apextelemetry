"use client";

import { useState } from "react";

import { CircuitMap, CornerChips } from "@/components/CircuitMap";
import { DriverLapPicker } from "@/components/DriverLapPicker";
import { SessionPicker, useSelection } from "@/components/SessionPicker";
import {
  ConfidenceBadge,
  Empty,
  ErrorPanel,
  Loading,
  NoteList,
  Panel,
  ProvenanceBadge,
  useAsync,
} from "@/components/ui";
import { api, type CornerComparison } from "@/lib/api";
import { DASH, delta as fmtDelta, metres, signedSpeed, withUncertainty } from "@/lib/format";

export default function CornersPage() {
  const [selection] = useSelection();
  const [selected, setSelected] = useState<number | null>(null);
  const [radius, setRadius] = useState(300);
  const [brakeMethod, setBrakeMethod] = useState<"auto" | "flag" | "deceleration">("auto");
  const [decel, setDecel] = useState(-3);

  const ready =
    selection.sessionKey != null && selection.driverA != null && selection.driverB != null;

  const analysis = useAsync(
    () =>
      api.corners({
        sessionKey: selection.sessionKey as number,
        driverA: selection.driverA as number,
        driverB: selection.driverB as number,
        lapA: selection.lapA,
        lapB: selection.lapB,
        radiusThreshold: radius,
        brakeMethod,
        decelThreshold: decel,
      }),
    [
      selection.sessionKey,
      selection.driverA,
      selection.driverB,
      selection.lapA,
      selection.lapB,
      radius,
      brakeMethod,
      decel,
    ],
    ready,
  );

  const rows = analysis.data?.comparison ?? [];
  const visible = selected ? rows.filter((row) => row.corner === selected) : rows;

  return (
    <div className="space-y-3">
      <Panel title="Selection" bodyClassName="space-y-3 p-3">
        <SessionPicker compact />
        <DriverLapPicker />
      </Panel>

      <Panel title="Detection settings" bodyClassName="grid gap-3 p-3 sm:grid-cols-3">
        <div>
          <label className="field-label" htmlFor="radius">
            Corner threshold · radius below {radius} m
          </label>
          <input
            id="radius"
            type="range"
            min={60}
            max={800}
            step={10}
            value={radius}
            onChange={(event) => setRadius(Number(event.target.value))}
            className="w-full accent-signal-info"
          />
          <p className="mt-1 text-2xs text-ink-faint">
            Raise to include fast kinks, lower to keep only heavy-braking corners.
          </p>
        </div>

        <div>
          <label className="field-label" htmlFor="method">
            Brake-onset method
          </label>
          <select
            id="method"
            className="field"
            value={brakeMethod}
            onChange={(event) =>
              setBrakeMethod(event.target.value as "auto" | "flag" | "deceleration")
            }
          >
            <option value="auto">Auto — brake channel, falling back to deceleration</option>
            <option value="flag">Brake channel only (measured)</option>
            <option value="deceleration">Deceleration threshold (estimated)</option>
          </select>
        </div>

        <div>
          <label className="field-label" htmlFor="decel">
            Deceleration threshold · {decel.toFixed(1)} m/s²
          </label>
          <input
            id="decel"
            type="range"
            min={-10}
            max={-1}
            step={0.5}
            value={decel}
            onChange={(event) => setDecel(Number(event.target.value))}
            className="w-full accent-signal-info"
            disabled={brakeMethod === "flag"}
          />
          <p className="mt-1 text-2xs text-ink-faint">
            Only used by the deceleration method. Drag off a straight at speed is roughly
            −1 to −2 m/s², so a threshold above that will catch lifts as well as braking.
          </p>
        </div>
      </Panel>

      {!ready ? (
        <Panel bodyClassName="p-0">
          <Empty title="Choose a session and two drivers to compare" />
        </Panel>
      ) : analysis.loading && !analysis.data ? (
        <Panel bodyClassName="p-0">
          <Loading label="Deriving circuit geometry and corner metrics" />
        </Panel>
      ) : analysis.error ? (
        <Panel bodyClassName="p-0">
          <ErrorPanel error={analysis.error} onRetry={analysis.reload} />
        </Panel>
      ) : analysis.data ? (
        <>
          <div className="grid gap-3 xl:grid-cols-[minmax(0,480px)_minmax(0,1fr)]">
            <Panel
              title="Circuit map"
              right={
                <span className="num text-2xs text-ink-faint">
                  {analysis.data.circuit.corners.length} corners ·{" "}
                  {metres(analysis.data.circuit.lap_length_m)}
                </span>
              }
              bodyClassName="p-2"
            >
              <CircuitMap
                circuit={analysis.data.circuit}
                selected={selected}
                onSelect={setSelected}
              />
            </Panel>

            <div className="space-y-3">
              <Panel title="Corners" bodyClassName="space-y-2 p-3">
                <CornerChips
                  corners={analysis.data.circuit.corners}
                  selected={selected}
                  onSelect={setSelected}
                />
                {selected && (
                  <button type="button" className="btn" onClick={() => setSelected(null)}>
                    Show all corners
                  </button>
                )}
              </Panel>

              <Panel title="Method" bodyClassName="space-y-2 p-3">
                {Object.entries(analysis.data.method.explanation).map(([key, value]) => (
                  <div key={key}>
                    <p className="text-2xs font-semibold uppercase tracking-wider text-ink-muted">
                      {key.replace(/_/g, " ")}
                    </p>
                    <p className="text-2xs leading-relaxed text-ink-faint">{value}</p>
                  </div>
                ))}
                <NoteList notes={analysis.data.circuit.notes} />
              </Panel>
            </div>
          </div>

          <Panel
            title={
              selected ? `Turn ${selected} comparison` : "Corner-by-corner comparison"
            }
            right={
              <span className="text-2xs text-ink-faint">
                A = #{selection.driverA} · B = #{selection.driverB}
              </span>
            }
            bodyClassName="overflow-auto"
          >
            {visible.length ? (
              <CornerTable rows={visible} />
            ) : (
              <Empty
                title="No corners detected at this threshold"
                detail="Raise the radius threshold above, or check whether this session has position data."
              />
            )}
          </Panel>

          {visible.length === 1 && <CornerDetail row={visible[0]} />}
        </>
      ) : null}
    </div>
  );
}

function CornerTable({ rows }: { rows: CornerComparison[] }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Corner</th>
          <th className="text-right">A brake point</th>
          <th className="text-right">B brake point</th>
          <th className="text-right">Δ brake</th>
          <th className="text-right">Δ min speed</th>
          <th className="text-right">Δ exit speed</th>
          <th className="text-right">Δt</th>
          <th>Confidence</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.corner}>
            <td className="num font-semibold text-ink">T{row.corner}</td>
            <td className="num text-right text-ink-muted">
              {row.a.brake
                ? withUncertainty(row.a.brake.onset_distance_m, row.a.brake.onset_uncertainty_m)
                : DASH}
            </td>
            <td className="num text-right text-ink-muted">
              {row.b.brake
                ? withUncertainty(row.b.brake.onset_distance_m, row.b.brake.onset_uncertainty_m)
                : DASH}
            </td>
            <td
              className={`num text-right ${
                row.brake_difference_significant ? "text-ink" : "text-ink-faint line-through"
              }`}
              title={
                row.brake_difference_significant
                  ? "Exceeds the combined sampling uncertainty of both traces."
                  : "Within sampling uncertainty — not a distinguishable difference."
              }
            >
              {row.brake_point_difference_m != null
                ? `${row.brake_point_difference_m > 0 ? "+" : "−"}${Math.abs(
                    row.brake_point_difference_m,
                  ).toFixed(0)} m`
                : DASH}
            </td>
            <td className="num text-right text-ink-muted">
              {signedSpeed(row.min_speed_difference_kph)}
            </td>
            <td className="num text-right text-ink-muted">
              {signedSpeed(row.exit_speed_difference_kph)}
            </td>
            <td
              className={`num text-right ${
                (row.time_delta_s ?? 0) > 0 ? "text-signal-alert" : "text-signal-ok"
              }`}
            >
              {fmtDelta(row.time_delta_s)}
            </td>
            <td>
              <ConfidenceBadge level={row.confidence} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CornerDetail({ row }: { row: CornerComparison }) {
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,340px)]">
      <Panel title={`Turn ${row.corner} — what the data shows`} bodyClassName="p-3 space-y-3">
        <p className="text-sm leading-relaxed text-ink">{row.interpretation}</p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Measurement</th>
              <th className="text-right">Driver A</th>
              <th className="text-right">Driver B</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            <MetricRow
              label="Entry speed"
              a={row.a.entry_speed_kph}
              b={row.b.entry_speed_kph}
              unit="km/h"
              kind="measured"
            />
            <MetricRow
              label="Minimum speed"
              a={row.a.min_speed_kph}
              b={row.b.min_speed_kph}
              unit="km/h"
              kind="measured"
            />
            <MetricRow
              label="Exit speed"
              a={row.a.exit_speed_kph}
              b={row.b.exit_speed_kph}
              unit="km/h"
              kind="measured"
            />
            <MetricRow
              label="Throttle application"
              a={row.a.throttle_application_m}
              b={row.b.throttle_application_m}
              unit="m"
              kind="calculated"
            />
            <MetricRow
              label="Time to full throttle"
              a={row.a.time_to_full_throttle_s}
              b={row.b.time_to_full_throttle_s}
              unit="s"
              digits={2}
              kind="calculated"
            />
            <MetricRow
              label="Gear at apex"
              a={row.a.gear_at_apex}
              b={row.b.gear_at_apex}
              unit=""
              digits={0}
              kind="measured"
            />
            <tr>
              <td className="text-ink-muted">Braking duration</td>
              <td className="num text-right">
                {row.a.brake?.duration_s != null ? `${row.a.brake.duration_s.toFixed(2)} s` : DASH}
              </td>
              <td className="num text-right">
                {row.b.brake?.duration_s != null ? `${row.b.brake.duration_s.toFixed(2)} s` : DASH}
              </td>
              <td>
                <ProvenanceBadge
                  kind={row.a.brake?.provenance ?? "unavailable"}
                />
              </td>
            </tr>
          </tbody>
        </table>
      </Panel>

      <Panel title="Caveats" bodyClassName="p-3 space-y-2">
        <NoteList notes={row.notes} />
        {!row.notes.length && (
          <p className="text-2xs text-ink-faint">
            No additional caveats recorded for this corner.
          </p>
        )}
      </Panel>
    </div>
  );
}

function MetricRow({
  label,
  a,
  b,
  unit,
  digits = 1,
  kind,
}: {
  label: string;
  a: number | null;
  b: number | null;
  unit: string;
  digits?: number;
  kind: "measured" | "calculated" | "estimated";
}) {
  const render = (value: number | null) =>
    value == null ? DASH : `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
  return (
    <tr>
      <td className="text-ink-muted">{label}</td>
      <td className="num text-right text-ink">{render(a)}</td>
      <td className="num text-right text-ink">{render(b)}</td>
      <td>
        <ProvenanceBadge kind={a == null && b == null ? "unavailable" : kind} />
      </td>
    </tr>
  );
}
