"use client";

import { useEffect, useMemo, useState } from "react";

import { SessionPicker, useSelection } from "@/components/SessionPicker";
import {
  Empty,
  ErrorPanel,
  FeedStatusBar,
  Loading,
  Panel,
  ProvenanceBadge,
  useAsync,
} from "@/components/ui";
import { api, type TimingRow } from "@/lib/api";
import {
  DASH,
  clockTime,
  compoundColour,
  compoundLetter,
  gap,
  lapTime,
  sectorTime,
  speed,
  teamColour,
} from "@/lib/format";

/** Poll interval while a session is genuinely live, in milliseconds.
 *  Kept at 5 s: OpenF1 publishes position roughly every 4 s and the
 *  Community tier allows 30 requests per minute across the whole app. */
const LIVE_POLL_MS = 5000;

export default function TimingPage() {
  const [selection] = useSelection();
  const sessionKey = selection.sessionKey;
  const [tick, setTick] = useState(0);

  const timing = useAsync(
    () => api.timing(sessionKey as number),
    [sessionKey, tick],
    sessionKey != null,
  );

  const isLive = timing.data?.status.mode === "live";

  useEffect(() => {
    if (!isLive) return;
    const id = setInterval(() => setTick((value) => value + 1), LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [isLive]);

  return (
    <div className="space-y-3">
      <Panel title="Weekend" bodyClassName="p-3">
        <SessionPicker />
      </Panel>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-graphite-700 bg-graphite-900 px-3 py-2">
        <FeedStatusBar status={timing.data?.status ?? null} />
        {isLive && (
          <span className="num text-2xs text-ink-faint">
            polling every {LIVE_POLL_MS / 1000}s · last {clockTime(new Date().toISOString())}
          </span>
        )}
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Panel
          title="Timing tower"
          right={
            timing.data?.session_best ? (
              <span className="num text-2xs text-signal-purple">
                SESSION BEST {lapTime(timing.data.session_best)}
              </span>
            ) : null
          }
          bodyClassName="overflow-auto"
        >
          {sessionKey == null ? (
            <Empty
              title="Select a weekend and session above"
              detail="Session identifiers are discovered from the provider, so the list reflects what actually ran."
            />
          ) : timing.loading && !timing.data ? (
            <Loading label="Fetching timing data" />
          ) : timing.error ? (
            <ErrorPanel error={timing.error} onRetry={timing.reload} />
          ) : !timing.data?.drivers.length ? (
            <Empty
              title="No timing data for this session"
              detail="The provider returned no laps. Sessions that were cancelled or have not run yet will look like this."
            />
          ) : (
            <TimingTower rows={timing.data.drivers} />
          )}
        </Panel>

        <div className="space-y-3">
          <Panel title="Conditions" bodyClassName="p-3">
            {timing.data?.weather ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
                <Reading label="Air" value={timing.data.weather.air_temperature} unit="°C" />
                <Reading label="Track" value={timing.data.weather.track_temperature} unit="°C" />
                <Reading label="Humidity" value={timing.data.weather.humidity} unit="%" />
                <Reading label="Wind" value={timing.data.weather.wind_speed} unit="m/s" />
                <Reading
                  label="Rainfall"
                  value={timing.data.weather.rainfall}
                  unit=""
                  formatter={(v) => (v > 0 ? "WET" : "DRY")}
                />
              </dl>
            ) : (
              <Empty title="No weather data" detail="This session has no weather readings in the feed." />
            )}
          </Panel>

          <Panel title="Race control" bodyClassName="max-h-[460px] overflow-auto">
            {timing.data?.race_control.length ? (
              <ul className="divide-y divide-graphite-800">
                {[...timing.data.race_control].reverse().map((message, index) => (
                  <li key={index} className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      {message.flag && message.flag !== "CLEAR" && (
                        <span
                          className="h-2 w-2 rounded-sm"
                          style={{ background: flagColour(message.flag) }}
                          aria-label={`${message.flag} flag`}
                        />
                      )}
                      <span className="num text-2xs text-ink-faint">
                        {clockTime(message.date)}
                      </span>
                      {message.lap_number != null && (
                        <span className="num text-2xs text-ink-faint">L{message.lap_number}</span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs leading-snug text-ink-muted">
                      {message.message ?? message.category ?? DASH}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No race control messages" />
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Reading({
  label,
  value,
  unit,
  formatter,
}: {
  label: string;
  value: number | null | undefined;
  unit: string;
  formatter?: (value: number) => string;
}) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-ink-faint">{label}</dt>
      <dd className="num text-sm text-ink">
        {value == null
          ? DASH
          : formatter
            ? formatter(value)
            : `${value.toFixed(1)}${unit}`}
      </dd>
    </div>
  );
}

function flagColour(flag: string): string {
  const map: Record<string, string> = {
    GREEN: "#2ee6a8",
    YELLOW: "#ffb020",
    "DOUBLE YELLOW": "#ffb020",
    RED: "#ff4d5e",
    BLUE: "#4da6ff",
    CHEQUERED: "#e9edf2",
    BLACK: "#07080a",
  };
  return map[flag.toUpperCase()] ?? "#5d6670";
}

function TimingTower({ rows }: { rows: TimingRow[] }) {
  const anyTyreData = useMemo(() => rows.some((row) => row.compound), [rows]);

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="w-10">Pos</th>
          <th className="w-32">Driver</th>
          <th className="w-24 text-right">Gap</th>
          <th className="w-24 text-right">Interval</th>
          <th className="w-24 text-right">Last</th>
          <th className="w-24 text-right">Best</th>
          <th className="w-16 text-right">S1</th>
          <th className="w-16 text-right">S2</th>
          <th className="w-16 text-right">S3</th>
          <th className="w-16 text-right">Trap</th>
          {anyTyreData && <th className="w-20">Tyre</th>}
          <th className="w-12 text-right">Laps</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.driver_number} className={row.retired ? "opacity-55" : undefined}>
            <td className="num text-ink-muted">{row.retired ? DASH : row.position ?? DASH}</td>
            <td>
              <div className="flex items-center gap-2">
                <span
                  className="h-4 w-0.5 rounded-sm"
                  style={{ background: teamColour(row.team_colour) }}
                  aria-hidden
                />
                <span className="num font-semibold text-ink">{row.acronym}</span>
                <span className="num text-2xs text-ink-faint">{row.driver_number}</span>
                {row.retired ? (
                  <span
                    className="rounded-sm bg-signal-alert/15 px-1 text-2xs font-bold text-signal-alert"
                    title={`Classified out after ${row.laps_completed} laps. Gap and interval are not shown because the feed's last values are frozen from the moment this car stopped.`}
                  >
                    OUT
                  </span>
                ) : (
                  row.in_pit && (
                    <span className="rounded-sm bg-signal-warn/15 px-1 text-2xs font-bold text-signal-warn">
                      PIT
                    </span>
                  )
                )}
              </div>
            </td>
            <td className="num text-right text-ink-muted">
              {row.retired ? DASH : gap(row.gap_to_leader)}
            </td>
            <td className="num text-right text-ink-muted">
              {row.retired ? DASH : gap(row.interval)}
            </td>
            <td className="num text-right text-ink">{lapTime(row.last_lap)}</td>
            <td
              className={`num text-right ${row.is_session_best ? "text-signal-purple" : "text-ink"}`}
            >
              {lapTime(row.best_lap)}
            </td>
            {[0, 1, 2].map((index) => (
              <td
                key={index}
                className={`num text-right ${
                  row.sectors[index] != null &&
                  row.best_sectors[index] != null &&
                  row.sectors[index]! <= row.best_sectors[index]!
                    ? "text-signal-ok"
                    : "text-ink-muted"
                }`}
              >
                {sectorTime(row.sectors[index])}
              </td>
            ))}
            <td className="num text-right text-ink-muted">{speed(row.speed_trap, 0)}</td>
            {anyTyreData && (
              <td>
                {row.compound ? (
                  <div className="flex items-center gap-1.5">
                    <span
                      className="flex h-4 w-4 items-center justify-center rounded-full border text-2xs font-bold"
                      style={{
                        borderColor: compoundColour(row.compound),
                        color: compoundColour(row.compound),
                      }}
                      title={row.compound}
                    >
                      {compoundLetter(row.compound)}
                    </span>
                    <span
                      className="num text-2xs text-ink-faint"
                      title="Tyre age is calculated from the stint's starting age plus laps completed, not measured directly."
                    >
                      {row.tyre_age != null ? `${row.tyre_age}L` : DASH}
                    </span>
                  </div>
                ) : (
                  <ProvenanceBadge kind="unavailable" />
                )}
              </td>
            )}
            <td className="num text-right text-ink-faint">{row.laps_completed}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
