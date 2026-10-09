"use client";

import { useEffect } from "react";

import { api, type LapInfo } from "@/lib/api";
import { lapTime, teamColour } from "@/lib/format";
import { useSelection } from "./SessionPicker";
import { useAsync } from "./ui";

/**
 * Pick two drivers and the laps to compare.
 *
 * Laps the backend flagged as unsuitable (pit-out, in-lap, under yellow,
 * well off the driver's own best) are still selectable — an engineer
 * sometimes wants exactly that lap — but they are visually demoted and the
 * reason is shown, rather than being silently dropped from the list.
 */
export function DriverLapPicker() {
  const [selection, update] = useSelection();
  const sessionKey = selection.sessionKey;

  const drivers = useAsync(
    () => api.drivers(sessionKey as number),
    [sessionKey],
    sessionKey != null,
  );

  // Seed with the first two team-mates' rivals so the page has something to
  // show immediately rather than four empty dropdowns.
  useEffect(() => {
    const list = drivers.data?.drivers ?? [];
    if (list.length >= 2 && selection.driverA == null && selection.driverB == null) {
      update({ driverA: list[0].driver_number, driverB: list[1].driver_number });
    }
  }, [drivers.data, selection.driverA, selection.driverB, update]);

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <DriverSelect
        label="Driver A"
        value={selection.driverA}
        onChange={(value) => update({ driverA: value, lapA: null })}
        options={drivers.data?.drivers ?? []}
        loading={drivers.loading}
      />
      <LapSelect
        label="Lap A"
        sessionKey={sessionKey}
        driverNumber={selection.driverA}
        value={selection.lapA}
        onChange={(value) => update({ lapA: value })}
      />
      <DriverSelect
        label="Driver B"
        value={selection.driverB}
        onChange={(value) => update({ driverB: value, lapB: null })}
        options={drivers.data?.drivers ?? []}
        loading={drivers.loading}
      />
      <LapSelect
        label="Lap B"
        sessionKey={sessionKey}
        driverNumber={selection.driverB}
        value={selection.lapB}
        onChange={(value) => update({ lapB: value })}
      />
    </div>
  );
}

function DriverSelect({
  label,
  value,
  onChange,
  options,
  loading,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  options: { driver_number: number; name_acronym?: string | null; full_name?: string | null; team_name?: string | null; team_colour?: string | null }[];
  loading: boolean;
}) {
  const selected = options.find((d) => d.driver_number === value);
  return (
    <div>
      <label className="field-label" htmlFor={label}>
        {label}
      </label>
      <div className="flex items-center gap-2">
        {selected && (
          <span
            className="h-7 w-0.5 shrink-0 rounded-sm"
            style={{ background: teamColour(selected.team_colour) }}
            aria-hidden
          />
        )}
        <select
          id={label}
          className="field"
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
          disabled={loading || !options.length}
        >
          <option value="">{loading ? "Loading…" : "Select"}</option>
          {options.map((driver) => (
            <option key={driver.driver_number} value={driver.driver_number}>
              {driver.driver_number} · {driver.name_acronym ?? driver.full_name} ·{" "}
              {driver.team_name ?? ""}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function LapSelect({
  label,
  sessionKey,
  driverNumber,
  value,
  onChange,
}: {
  label: string;
  sessionKey: number | null;
  driverNumber: number | null;
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  const laps = useAsync(
    () => api.laps(sessionKey as number, driverNumber as number),
    [sessionKey, driverNumber],
    sessionKey != null && driverNumber != null,
  );

  const list: LapInfo[] = (laps.data?.laps ?? []).filter((lap) => lap.lap_duration != null);
  const best = list.reduce<LapInfo | null>(
    (acc, lap) =>
      lap.is_valid && (acc == null || (lap.lap_duration ?? Infinity) < (acc.lap_duration ?? Infinity))
        ? lap
        : acc,
    null,
  );

  const selected = list.find((lap) => lap.lap_number === value);

  return (
    <div>
      <label className="field-label" htmlFor={label}>
        {label}
      </label>
      <select
        id={label}
        className="field"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
        disabled={laps.loading || !list.length}
      >
        <option value="">
          {laps.loading
            ? "Loading…"
            : best
              ? `Fastest clean lap (L${best.lap_number} · ${lapTime(best.lap_duration)})`
              : "Fastest lap"}
        </option>
        {list.map((lap) => (
          <option key={lap.lap_number} value={lap.lap_number ?? undefined}>
            L{lap.lap_number} · {lapTime(lap.lap_duration)}
            {lap.is_valid ? "" : " · flagged"}
          </option>
        ))}
      </select>
      {selected && !selected.is_valid && (
        <p className="mt-1 text-2xs leading-snug text-signal-warn">
          {selected.flags.join(" · ")}
        </p>
      )}
    </div>
  );
}
