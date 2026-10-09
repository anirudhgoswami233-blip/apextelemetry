"use client";

import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";

import { DriverLapPicker } from "@/components/DriverLapPicker";
import { SessionPicker, useSelection } from "@/components/SessionPicker";
import {
  CHART_BASE,
  Chart,
  Empty,
  ErrorPanel,
  Loading,
  NoteList,
  Panel,
  ProvenanceBadge,
  axis,
  useAsync,
} from "@/components/ui";
import { api, type CompareResponse } from "@/lib/api";
import { DASH, delta as fmtDelta, lapTime, metres, pairs } from "@/lib/format";

const COLOUR_A = "#4da6ff";
const COLOUR_B = "#ffb020";
const GROUP = "telemetry-lab";

export default function TelemetryPage() {
  const [selection] = useSelection();
  const [mode, setMode] = useState<"truncate" | "normalize">("truncate");

  const ready =
    selection.sessionKey != null && selection.driverA != null && selection.driverB != null;

  const comparison = useAsync(
    () =>
      api.compare({
        sessionKey: selection.sessionKey as number,
        driverA: selection.driverA as number,
        driverB: selection.driverB as number,
        lapA: selection.lapA,
        lapB: selection.lapB,
        mode,
      }),
    [selection.sessionKey, selection.driverA, selection.driverB, selection.lapA, selection.lapB, mode],
    ready,
  );

  return (
    <div className="space-y-3">
      <Panel title="Selection" bodyClassName="space-y-3 p-3">
        <SessionPicker compact />
        <DriverLapPicker />
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <label className="field-label" htmlFor="align">
              Distance alignment
            </label>
            <select
              id="align"
              className="field max-w-xs"
              value={mode}
              onChange={(event) => setMode(event.target.value as "truncate" | "normalize")}
            >
              <option value="truncate">Truncate to the shorter trace (honest)</option>
              <option value="normalize">Normalise onto a shared length (assumes even drift)</option>
            </select>
          </div>
          {comparison.data && (
            <a
              className="btn btn-primary"
              href={api.compareCsvUrl({
                sessionKey: selection.sessionKey as number,
                driverA: selection.driverA as number,
                driverB: selection.driverB as number,
                lapA: selection.lapA,
                lapB: selection.lapB,
                mode,
              })}
            >
              Export CSV
            </a>
          )}
        </div>
      </Panel>

      {!ready ? (
        <Panel bodyClassName="p-0">
          <Empty
            title="Choose a session and two drivers"
            detail="Leave a lap unset to use that driver's fastest clean lap."
          />
        </Panel>
      ) : comparison.loading && !comparison.data ? (
        <Panel bodyClassName="p-0">
          <Loading label="Fetching and aligning telemetry" />
        </Panel>
      ) : comparison.error ? (
        <Panel bodyClassName="p-0">
          <ErrorPanel error={comparison.error} onRetry={comparison.reload} />
        </Panel>
      ) : comparison.data ? (
        <Comparison data={comparison.data} />
      ) : null}
    </div>
  );
}

function Comparison({ data }: { data: CompareResponse }) {
  const { delta, a, b } = data;

  const labelA = `#${a.driver_number} L${a.lap_number}`;
  const labelB = `#${b.driver_number} L${b.lap_number}`;

  const speedOption = useMemo<EChartsOption>(
    () => ({
      ...CHART_BASE,
      xAxis: axis("distance (m)", { min: 0, max: Math.max(a.length_m, b.length_m) }),
      yAxis: axis("km/h", { scale: true }),
      legend: {
        data: [labelA, labelB],
        textStyle: { color: "#8d97a3", fontSize: 10 },
        top: 0,
        right: 0,
      },
      series: [
        line(labelA, pairs(a.channels.distance, a.channels.speed), COLOUR_A),
        line(labelB, pairs(b.channels.distance, b.channels.speed), COLOUR_B),
      ],
    }),
    [a, b, labelA, labelB],
  );

  const deltaOption = useMemo<EChartsOption>(() => {
    const points = pairs(delta.distance, delta.delta);
    return {
      ...CHART_BASE,
      xAxis: axis("distance (m)", { min: 0 }),
      yAxis: axis("Δt (s)", { scale: true }),
      series: [
        {
          type: "line",
          name: "delta",
          data: points,
          showSymbol: false,
          lineStyle: { width: 1.5, color: "#c77dff" },
          areaStyle: { color: "rgba(199,125,255,0.10)" },
          markLine: {
            silent: true,
            symbol: "none",
            data: [{ yAxis: 0 }],
            lineStyle: { color: "#3a444f", type: "dashed", width: 1 },
            label: { show: false },
          },
        },
      ],
    };
  }, [delta]);

  const pedalsOption = useMemo<EChartsOption>(
    () => ({
      ...CHART_BASE,
      grid: { ...CHART_BASE.grid, top: 16 },
      xAxis: axis("", { min: 0, max: Math.max(a.length_m, b.length_m) }),
      yAxis: axis("%", { min: 0, max: 105 }),
      series: [
        line(`${labelA} throttle`, pairs(a.channels.distance, a.channels.throttle), COLOUR_A),
        line(`${labelB} throttle`, pairs(b.channels.distance, b.channels.throttle), COLOUR_B),
        step(`${labelA} brake`, pairs(a.channels.distance, a.channels.brake), "#ff4d5e"),
        step(`${labelB} brake`, pairs(b.channels.distance, b.channels.brake), "#ff8a94"),
      ],
    }),
    [a, b, labelA, labelB],
  );

  const gearOption = useMemo<EChartsOption>(
    () => ({
      ...CHART_BASE,
      grid: { ...CHART_BASE.grid, top: 16 },
      xAxis: axis("", { min: 0, max: Math.max(a.length_m, b.length_m) }),
      yAxis: axis("gear", { min: 0, max: 9, interval: 1 }),
      series: [
        step(labelA, pairs(a.channels.distance, a.channels.gear), COLOUR_A),
        step(labelB, pairs(b.channels.distance, b.channels.gear), COLOUR_B),
      ],
    }),
    [a, b, labelA, labelB],
  );

  const rpmOption = useMemo<EChartsOption>(
    () => ({
      ...CHART_BASE,
      grid: { ...CHART_BASE.grid, top: 16 },
      xAxis: axis("distance (m)", { min: 0, max: Math.max(a.length_m, b.length_m) }),
      yAxis: axis("rpm", { scale: true }),
      series: [
        line(labelA, pairs(a.channels.distance, a.channels.rpm), COLOUR_A),
        line(labelB, pairs(b.channels.distance, b.channels.rpm), COLOUR_B),
      ],
    }),
    [a, b, labelA, labelB],
  );

  const biggestLoss = useMemo(
    () => [...delta.regions].sort((x, y) => y.delta_change_s - x.delta_change_s)[0],
    [delta.regions],
  );
  const biggestGain = useMemo(
    () => [...delta.regions].sort((x, y) => x.delta_change_s - y.delta_change_s)[0],
    [delta.regions],
  );

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-4">
        <Stat label="Final delta" value={`${fmtDelta(delta.final_delta_s)} s`} kind="calculated"
          hint={`${labelB} relative to ${labelA}. Positive means B is behind.`} />
        <Stat label={`${labelA} lap`} value={lapTime(a.lap_duration)} kind="measured" />
        <Stat label={`${labelB} lap`} value={lapTime(b.lap_duration)} kind="measured" />
        <Stat
          label="Event resolution"
          value={`±${((delta.spatial_resolution_m ?? 0) / 2).toFixed(0)} m`}
          kind="calculated"
          hint="Half the worst-case telemetry sample spacing. Nothing on these charts can be located more precisely."
        />
      </div>

      <Panel title="Speed vs distance" bodyClassName="p-2">
        <Chart option={speedOption} height={260} group={GROUP} />
      </Panel>

      <Panel
        title="Cumulative lap delta"
        right={
          <span className="text-2xs text-ink-faint">
            rising = {labelB} losing time
          </span>
        }
        bodyClassName="p-2"
      >
        <Chart option={deltaOption} height={180} group={GROUP} />
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Throttle and brake" bodyClassName="p-2">
          <Chart option={pedalsOption} height={180} group={GROUP} />
        </Panel>
        <Panel title="Gear" bodyClassName="p-2">
          <Chart option={gearOption} height={180} group={GROUP} />
        </Panel>
      </div>

      <Panel title="RPM" bodyClassName="p-2">
        <Chart option={rpmOption} height={160} group={GROUP} />
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Sector comparison" bodyClassName="p-0">
          <table className="data-table">
            <thead>
              <tr>
                <th>Sector</th>
                <th className="text-right">{labelA}</th>
                <th className="text-right">{labelB}</th>
                <th className="text-right">Δ</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {delta.sectors.map((sector) => (
                <tr key={sector.sector}>
                  <td className="text-ink-muted">S{sector.sector}</td>
                  <td className="num text-right">
                    {sector.a_seconds?.toFixed(3) ?? DASH}
                  </td>
                  <td className="num text-right">
                    {sector.b_seconds?.toFixed(3) ?? DASH}
                  </td>
                  <td
                    className={`num text-right ${
                      (sector.delta_seconds ?? 0) > 0 ? "text-signal-alert" : "text-signal-ok"
                    }`}
                  >
                    {fmtDelta(sector.delta_seconds)}
                  </td>
                  <td>
                    <ProvenanceBadge kind={sector.provenance} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Largest time swings" bodyClassName="p-3 space-y-3">
          {biggestLoss && biggestLoss.delta_change_s > 0 && (
            <Swing
              title={`${labelB} loses most here`}
              region={biggestLoss}
              tone="alert"
            />
          )}
          {biggestGain && biggestGain.delta_change_s < 0 && (
            <Swing
              title={`${labelB} gains most here`}
              region={biggestGain}
              tone="ok"
            />
          )}
          {!delta.regions.length && (
            <Empty
              title="No significant swings"
              detail="No stretch moved the delta by more than the noise floor."
            />
          )}
        </Panel>
      </div>

      <Panel title="How this was computed" bodyClassName="space-y-2 p-3">
        <p className="text-2xs leading-relaxed text-ink-faint">
          Distance is <strong className="text-ink-muted">calculated</strong> by trapezoidal
          integration of the measured speed channel over time; the provider publishes no
          distance channel. Alignment mode:{" "}
          <strong className="text-ink-muted">{delta.alignment_mode}</strong>. Integrated lap
          lengths: {metres(delta.length_a_m)} and {metres(delta.length_b_m)}.
        </p>
        <NoteList notes={[...delta.notes, ...a.warnings, ...b.warnings]} />
      </Panel>
    </div>
  );
}

function Swing({
  title,
  region,
  tone,
}: {
  title: string;
  region: { start_m: number; end_m: number; delta_change_s: number; peak_speed_difference_kph: number | null };
  tone: "alert" | "ok";
}) {
  return (
    <div
      className={`rounded border px-3 py-2 ${
        tone === "alert"
          ? "border-signal-alert/30 bg-signal-alert/5"
          : "border-signal-ok/30 bg-signal-ok/5"
      }`}
    >
      <p className="text-2xs font-semibold uppercase tracking-wider text-ink-muted">{title}</p>
      <p className="num mt-1 text-lg text-ink">{fmtDelta(region.delta_change_s)} s</p>
      <p className="num text-2xs text-ink-faint">
        {metres(region.start_m)} → {metres(region.end_m)}
        {region.peak_speed_difference_kph != null &&
          ` · peak Δv ${region.peak_speed_difference_kph.toFixed(1)} km/h`}
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  kind,
  hint,
}: {
  label: string;
  value: string;
  kind: "measured" | "calculated" | "estimated";
  hint?: string;
}) {
  return (
    <div className="panel p-3" title={hint}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-2xs uppercase tracking-wider text-ink-faint">{label}</p>
        <ProvenanceBadge kind={kind} />
      </div>
      <p className="num mt-1.5 text-xl text-ink">{value}</p>
    </div>
  );
}

function line(name: string, data: [number, number][], colour: string) {
  return {
    type: "line" as const,
    name,
    data,
    showSymbol: false,
    sampling: "lttb" as const,
    lineStyle: { width: 1.4, color: colour },
    emphasis: { focus: "series" as const },
  };
}

function step(name: string, data: [number, number][], colour: string) {
  return {
    type: "line" as const,
    name,
    data,
    step: "end" as const,
    showSymbol: false,
    lineStyle: { width: 1.2, color: colour },
  };
}
