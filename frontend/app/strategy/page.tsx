"use client";

import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";

import { SessionPicker, useSelection } from "@/components/SessionPicker";
import {
  CHART_BASE,
  Chart,
  Empty,
  ErrorPanel,
  Loading,
  Note,
  NoteList,
  Panel,
  ProvenanceBadge,
  axis,
  useAsync,
} from "@/components/ui";
import { api, type StintSummary } from "@/lib/api";
import { DASH, compoundColour, compoundLetter, lapTime } from "@/lib/format";

export default function StrategyPage() {
  const [selection] = useSelection();
  const [fuelEffect, setFuelEffect] = useState(0.055);
  const [focusDriver, setFocusDriver] = useState<number | null>(null);

  const sessionKey = selection.sessionKey;
  const strategy = useAsync(
    () => api.strategy(sessionKey as number, fuelEffect),
    [sessionKey, fuelEffect],
    sessionKey != null,
  );
  const drivers = useAsync(
    () => api.drivers(sessionKey as number),
    [sessionKey],
    sessionKey != null,
  );

  const acronyms = useMemo(() => {
    const map = new Map<number, string>();
    drivers.data?.drivers.forEach((d) =>
      map.set(d.driver_number, d.name_acronym ?? String(d.driver_number)),
    );
    return map;
  }, [drivers.data]);

  const stints = strategy.data?.stints ?? [];
  const focused = focusDriver ? stints.filter((s) => s.driver_number === focusDriver) : stints;

  return (
    <div className="space-y-3">
      <Panel title="Session" bodyClassName="p-3">
        <SessionPicker compact />
      </Panel>

      <Panel title="Model assumptions" bodyClassName="p-3 space-y-2">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="field-label" htmlFor="fuel">
              Fuel effect · {fuelEffect.toFixed(3)} s/lap
            </label>
            <input
              id="fuel"
              type="range"
              min={0}
              max={0.12}
              step={0.005}
              value={fuelEffect}
              onChange={(event) => setFuelEffect(Number(event.target.value))}
              className="w-full accent-signal-warn"
            />
          </div>
          <div>
            <label className="field-label" htmlFor="focus">
              Focus on one car
            </label>
            <select
              id="focus"
              className="field"
              value={focusDriver ?? ""}
              onChange={(event) =>
                setFocusDriver(event.target.value ? Number(event.target.value) : null)
              }
            >
              <option value="">All cars</option>
              {drivers.data?.drivers.map((driver) => (
                <option key={driver.driver_number} value={driver.driver_number}>
                  {driver.driver_number} · {driver.name_acronym ?? driver.full_name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <Note>
          Fuel burn makes lap times fall as a stint goes on, so a raw regression understates
          degradation. The corrected slope adds this figure back. It is an assumption, not a
          measurement — the provider publishes no fuel data.
        </Note>
      </Panel>

      {sessionKey == null ? (
        <Panel bodyClassName="p-0">
          <Empty title="Select a session" />
        </Panel>
      ) : strategy.loading && !strategy.data ? (
        <Panel bodyClassName="p-0">
          <Loading label="Fetching stints and fitting degradation" />
        </Panel>
      ) : strategy.error ? (
        <Panel bodyClassName="p-0">
          <ErrorPanel error={strategy.error} onRetry={strategy.reload} />
        </Panel>
      ) : !stints.length ? (
        <Panel bodyClassName="p-0">
          <Empty
            title="No stint data for this session"
            detail="Practice and qualifying sessions often have no stint records in the feed."
          />
        </Panel>
      ) : (
        <>
          <Panel title="Stint timeline" bodyClassName="overflow-auto p-3">
            <StintTimeline stints={stints} acronyms={acronyms} />
          </Panel>

          <div className="grid gap-3 xl:grid-cols-2">
            <Panel title="Lap time vs tyre age" bodyClassName="p-2">
              <PaceChart stints={focused} acronyms={acronyms} />
            </Panel>

            <Panel title="Degradation fits" bodyClassName="overflow-auto">
              <DegradationTable stints={focused} acronyms={acronyms} />
            </Panel>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <Panel title="Pit stops" bodyClassName="overflow-auto">
              {strategy.data?.pit_stops.length ? (
                <>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Car</th>
                        <th className="text-right">Stops</th>
                        <th className="text-right">Best</th>
                        <th className="text-right">Median</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.data.pit_stops.map((stop) => (
                        <tr key={stop.driver_number}>
                          <td className="num text-ink">
                            {acronyms.get(stop.driver_number) ?? stop.driver_number}
                          </td>
                          <td className="num text-right text-ink-muted">{stop.stops}</td>
                          <td className="num text-right text-ink">
                            {stop.best_s?.toFixed(2) ?? DASH}
                          </td>
                          <td className="num text-right text-ink-muted">
                            {stop.median_s?.toFixed(2) ?? DASH}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="p-3">
                    <Note>{strategy.data.pit_stops[0].note}</Note>
                  </div>
                </>
              ) : (
                <Empty title="No pit stops recorded" />
              )}
            </Panel>

            <UndercutCalculator stints={focused} />
          </div>

          {strategy.data?.neutralised_laps.length ? (
            <Panel title="Neutralised laps" bodyClassName="p-3">
              <p className="num text-2xs leading-relaxed text-ink-muted">
                {strategy.data.neutralised_laps.join(", ")}
              </p>
              <div className="mt-2">
                <Note>
                  These laps were shown a yellow or red flag, or ran under a safety car according
                  to race control. They are excluded from every degradation fit above.
                </Note>
              </div>
            </Panel>
          ) : null}
        </>
      )}
    </div>
  );
}

function StintTimeline({
  stints,
  acronyms,
}: {
  stints: StintSummary[];
  acronyms: Map<number, string>;
}) {
  const byDriver = useMemo(() => {
    const map = new Map<number, StintSummary[]>();
    stints.forEach((stint) => {
      const list = map.get(stint.driver_number) ?? [];
      list.push(stint);
      map.set(stint.driver_number, list);
    });
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [stints]);

  const maxLap = Math.max(1, ...stints.map((s) => s.lap_end ?? 0));

  return (
    <div className="space-y-1.5">
      {byDriver.map(([driver, list]) => (
        <div key={driver} className="flex items-center gap-2">
          <span className="num w-12 shrink-0 text-2xs text-ink-muted">
            {acronyms.get(driver) ?? driver}
          </span>
          <div className="relative h-5 flex-1 rounded-sm bg-graphite-850">
            {list.map((stint) => {
              const start = ((stint.lap_start ?? 1) - 1) / maxLap;
              const width = ((stint.lap_end ?? 1) - (stint.lap_start ?? 1) + 1) / maxLap;
              return (
                <div
                  key={`${stint.stint_number}-${stint.lap_start}`}
                  className="absolute top-0 flex h-full items-center justify-center rounded-sm border text-2xs font-bold"
                  style={{
                    left: `${start * 100}%`,
                    width: `${width * 100}%`,
                    borderColor: compoundColour(stint.compound),
                    background: `${compoundColour(stint.compound)}22`,
                    color: compoundColour(stint.compound),
                  }}
                  title={`${stint.compound ?? "unknown"} · laps ${stint.lap_start}-${stint.lap_end} · ${stint.laps_completed} laps`}
                >
                  {width > 0.06 ? compoundLetter(stint.compound) : ""}
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <p className="num pt-1 text-2xs text-ink-faint">lap 1 → {maxLap}</p>
    </div>
  );
}

function PaceChart({
  stints,
  acronyms,
}: {
  stints: StintSummary[];
  acronyms: Map<number, string>;
}) {
  const option = useMemo<EChartsOption>(() => {
    const series = stints
      .filter((stint) => stint.lap_times.length > 2)
      .slice(0, 12)
      .map((stint) => ({
        type: "scatter" as const,
        name: `${acronyms.get(stint.driver_number) ?? stint.driver_number} · ${stint.compound ?? "?"} · S${stint.stint_number}`,
        symbolSize: 5,
        itemStyle: { color: compoundColour(stint.compound), opacity: 0.8 },
        data: stint.lap_times.map(([, duration, age]) => [age, duration]),
      }));

    return {
      ...CHART_BASE,
      legend: {
        type: "scroll",
        textStyle: { color: "#8d97a3", fontSize: 9 },
        top: 0,
      },
      grid: { ...CHART_BASE.grid, top: 36 },
      xAxis: axis("tyre age (laps)", { min: 0 }),
      yAxis: axis("lap time (s)", { scale: true }),
      series,
    };
  }, [stints, acronyms]);

  if (!stints.some((stint) => stint.lap_times.length > 2)) {
    return <Empty title="Not enough clean laps to plot pace against tyre age" />;
  }
  return <Chart option={option} height={300} />;
}

function DegradationTable({
  stints,
  acronyms,
}: {
  stints: StintSummary[];
  acronyms: Map<number, string>;
}) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Car</th>
          <th>Stint</th>
          <th>Compound</th>
          <th className="text-right">Laps</th>
          <th className="text-right">Median</th>
          <th className="text-right">Deg s/lap</th>
          <th className="text-right">± stderr</th>
          <th className="text-right">R²</th>
          <th>Verdict</th>
        </tr>
      </thead>
      <tbody>
        {stints.map((stint) => {
          const fit = stint.degradation;
          const meaningful =
            fit?.slope_s_per_lap != null &&
            fit.slope_stderr != null &&
            fit.sample_size >= 5 &&
            Math.abs(fit.slope_s_per_lap) > fit.slope_stderr;
          return (
            <tr key={`${stint.driver_number}-${stint.stint_number}-${stint.lap_start}`}>
              <td className="num text-ink">
                {acronyms.get(stint.driver_number) ?? stint.driver_number}
              </td>
              <td className="num text-ink-muted">{stint.stint_number ?? DASH}</td>
              <td>
                <span
                  className="num text-2xs font-bold"
                  style={{ color: compoundColour(stint.compound) }}
                >
                  {stint.compound ?? DASH}
                </span>
              </td>
              <td className="num text-right text-ink-muted">{fit?.sample_size ?? 0}</td>
              <td className="num text-right text-ink-muted">{lapTime(stint.median_s)}</td>
              <td className="num text-right text-ink">
                {fit?.slope_s_per_lap != null ? fit.slope_s_per_lap.toFixed(3) : DASH}
              </td>
              <td className="num text-right text-ink-faint">
                {fit?.slope_stderr != null ? fit.slope_stderr.toFixed(3) : DASH}
              </td>
              <td className="num text-right text-ink-faint">
                {fit?.r_squared != null ? fit.r_squared.toFixed(2) : DASH}
              </td>
              <td>
                {meaningful ? (
                  <ProvenanceBadge kind="estimated" />
                ) : (
                  <span
                    className="text-2xs text-ink-faint"
                    title="The fitted slope is not larger than its own standard error, or the sample is too small."
                  >
                    no trend
                  </span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function UndercutCalculator({ stints }: { stints: StintSummary[] }) {
  const [gainPerLap, setGainPerLap] = useState(1.0);
  const [laps, setLaps] = useState(2);

  const bestDeg = useMemo(() => {
    const slopes = stints
      .map((s) => s.degradation?.slope_s_per_lap)
      .filter((v): v is number => v != null);
    return slopes.length ? slopes.reduce((a, b) => a + b, 0) / slopes.length : null;
  }, [stints]);

  const scenario = useAsync(
    () =>
      api.undercut({
        freshTyreGainPerLap: gainPerLap,
        lapsUntilRivalPits: laps,
        degradationPerLap: bestDeg,
      }),
    [gainPerLap, laps, bestDeg],
  );

  return (
    <Panel title="Undercut scenario" bodyClassName="space-y-3 p-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="field-label" htmlFor="gain">
            Fresh-tyre advantage · {gainPerLap.toFixed(2)} s/lap
          </label>
          <input
            id="gain"
            type="range"
            min={0}
            max={3}
            step={0.05}
            value={gainPerLap}
            onChange={(event) => setGainPerLap(Number(event.target.value))}
            className="w-full accent-signal-warn"
          />
        </div>
        <div>
          <label className="field-label" htmlFor="laps">
            Laps before the rival pits · {laps}
          </label>
          <input
            id="laps"
            type="range"
            min={1}
            max={6}
            step={1}
            value={laps}
            onChange={(event) => setLaps(Number(event.target.value))}
            className="w-full accent-signal-warn"
          />
        </div>
      </div>

      {scenario.data ? (
        <div className="space-y-2">
          <div className="flex items-baseline gap-2">
            <span
              className={`num text-2xl ${
                scenario.data.net_seconds > 0 ? "text-signal-ok" : "text-signal-alert"
              }`}
            >
              {scenario.data.net_seconds > 0 ? "+" : "−"}
              {Math.abs(scenario.data.net_seconds).toFixed(2)} s
            </span>
            <ProvenanceBadge kind="estimated" />
          </div>
          <table className="data-table">
            <tbody>
              {scenario.data.terms.map((term, index) => (
                <tr key={index}>
                  <td className="text-ink-muted">{term.label}</td>
                  <td
                    className={`num text-right ${
                      term.seconds >= 0 ? "text-signal-ok" : "text-signal-alert"
                    }`}
                  >
                    {term.seconds >= 0 ? "+" : "−"}
                    {Math.abs(term.seconds).toFixed(2)}
                  </td>
                  <td className="max-w-[220px] whitespace-normal text-2xs text-ink-faint">
                    {term.basis}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <NoteList notes={[scenario.data.caveat]} />
        </div>
      ) : scenario.loading ? (
        <Loading label="Computing scenario" />
      ) : (
        <Empty title="Adjust the sliders to model an undercut" />
      )}
    </Panel>
  );
}
