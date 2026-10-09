"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useCallback, useEffect, useMemo } from "react";

import { api, type SessionInfo } from "@/lib/api";
import { localTime, sessionLabel } from "@/lib/format";
import { useAsync } from "./ui";

/**
 * Season → Grand Prix → Session.
 *
 * Selection lives in the URL, so every view is a shareable link and the back
 * button works. Session keys are always discovered from the provider; nothing
 * in this application hardcodes one.
 */

export interface Selection {
  year: number | null;
  meetingKey: number | null;
  sessionKey: number | null;
  driverA: number | null;
  driverB: number | null;
  lapA: number | null;
  lapB: number | null;
}

export function useSelection(): [Selection, (patch: Partial<Selection>) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const selection = useMemo<Selection>(() => {
    const read = (key: string) => {
      const value = params.get(key);
      if (value == null || value === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    return {
      year: read("year"),
      meetingKey: read("meeting"),
      sessionKey: read("session"),
      driverA: read("a"),
      driverB: read("b"),
      lapA: read("lapA"),
      lapB: read("lapB"),
    };
  }, [params]);

  const update = useCallback(
    (patch: Partial<Selection>) => {
      const next = new URLSearchParams(params.toString());
      const keys: Record<keyof Selection, string> = {
        year: "year",
        meetingKey: "meeting",
        sessionKey: "session",
        driverA: "a",
        driverB: "b",
        lapA: "lapA",
        lapB: "lapB",
      };
      (Object.keys(patch) as (keyof Selection)[]).forEach((key) => {
        const value = patch[key];
        if (value == null) next.delete(keys[key]);
        else next.set(keys[key], String(value));
      });
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );

  return [selection, update];
}

export function SessionPicker({ compact = false }: { compact?: boolean }) {
  const [selection, update] = useSelection();

  const seasons = useAsync(() => api.seasons(), []);
  const year = selection.year ?? seasons.data?.seasons[0] ?? null;

  const meetings = useAsync(() => api.meetings(year as number), [year], year != null);
  const sessions = useAsync(
    () => api.weekend(selection.meetingKey as number),
    [selection.meetingKey],
    selection.meetingKey != null,
  );

  // Default to the most recent weekend that has actually started, so the
  // workstation is never sitting empty waiting for three dropdowns.
  //
  // Taking the last entry in the calendar is wrong: in April that would select
  // a race in December. The latest *started* meeting is what "most recent"
  // means to someone opening this page.
  useEffect(() => {
    if (selection.meetingKey != null) return;
    const list = meetings.data?.meetings ?? [];
    if (!list.length) return;

    const now = Date.now();
    const started = list.filter(
      (m) => m.date_start && new Date(m.date_start).getTime() <= now,
    );
    const pick = started.length ? started[started.length - 1] : list[0];
    update({ year, meetingKey: pick.meeting_key });
  }, [meetings.data, selection.meetingKey, year, update]);

  // Default to the most recent session that has run.
  //
  // Reaching for `session_type === "Race"` breaks on a weekend in progress: on
  // the Saturday of a Sprint weekend the race is in the schedule but has no
  // laps, and the workstation opens on an error instead of on the data that is
  // already there.
  //
  // The meeting_key guard matters as much. `sessions.data` still holds the
  // previous weekend's list while the new fetch is in flight, so without it
  // this effect commits a session key belonging to a weekend the user has
  // already navigated away from — which leaves the dropdown blank and sends
  // every panel asking for data that was never part of this selection.
  useEffect(() => {
    const list = sessions.data?.sessions ?? [];
    if (!list.length) return;
    if (sessions.data?.meeting_key !== selection.meetingKey) return;

    const current = selection.sessionKey;
    if (current != null && list.some((s) => s.session_key === current)) return;

    const completed = list.filter((s) => s.completed !== false);
    const pick = completed.length ? completed[completed.length - 1] : list[0];
    update({ sessionKey: pick.session_key, lapA: null, lapB: null });
  }, [sessions.data, selection.sessionKey, selection.meetingKey, update]);

  return (
    <div
      className={`grid gap-3 ${compact ? "grid-cols-1 sm:grid-cols-3" : "grid-cols-1 md:grid-cols-3"}`}
    >
      <div>
        <label className="field-label" htmlFor="season">
          Season
        </label>
        <select
          id="season"
          className="field"
          value={year ?? ""}
          onChange={(event) =>
            update({
              year: Number(event.target.value),
              meetingKey: null,
              sessionKey: null,
              driverA: null,
              driverB: null,
              lapA: null,
              lapB: null,
            })
          }
          disabled={seasons.loading}
        >
          {seasons.data?.seasons.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="field-label" htmlFor="meeting">
          Grand Prix
        </label>
        <select
          id="meeting"
          className="field"
          value={selection.meetingKey ?? ""}
          onChange={(event) =>
            update({
              meetingKey: Number(event.target.value),
              sessionKey: null,
              lapA: null,
              lapB: null,
            })
          }
          disabled={meetings.loading || !meetings.data?.meetings.length}
        >
          <option value="">
            {meetings.loading ? "Loading…" : "Select a weekend"}
          </option>
          {meetings.data?.meetings.map((meeting) => (
            <option key={meeting.meeting_key} value={meeting.meeting_key}>
              {meeting.meeting_name ?? meeting.location ?? `Meeting ${meeting.meeting_key}`}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="field-label" htmlFor="session">
          Session
        </label>
        <select
          id="session"
          className="field"
          value={selection.sessionKey ?? ""}
          onChange={(event) =>
            update({ sessionKey: Number(event.target.value), lapA: null, lapB: null })
          }
          disabled={sessions.loading || !sessions.data?.sessions.length}
        >
          <option value="">{sessions.loading ? "Loading…" : "Select a session"}</option>
          {sessions.data?.sessions.map((session) => (
            <option key={session.session_key} value={session.session_key}>
              {sessionLabel(session)}
              {session.completed === false ? " · not yet run" : ""}
            </option>
          ))}
        </select>
      </div>

      {!compact && sessions.data?.sessions.length ? (
        <div className="md:col-span-3">
          <WeekendOverview sessions={sessions.data.sessions} active={selection.sessionKey} />
        </div>
      ) : null}
    </div>
  );
}

function WeekendOverview({
  sessions,
  active,
}: {
  sessions: SessionInfo[];
  active: number | null;
}) {
  const [, update] = useSelection();
  return (
    <div className="flex flex-wrap gap-1.5">
      {sessions.map((session) => {
        const isActive = session.session_key === active;
        const completed = session.completed !== false;
        return (
          <button
            key={session.session_key}
            type="button"
            onClick={() => update({ sessionKey: session.session_key, lapA: null, lapB: null })}
            className={`rounded border px-2 py-1 text-left transition-colors ${
              isActive
                ? "border-signal-info/50 bg-signal-info/10"
                : "border-graphite-700 bg-graphite-850 hover:border-graphite-500"
            }`}
          >
            <div
              className={`text-2xs font-semibold uppercase tracking-wider ${
                isActive ? "text-signal-info" : completed ? "text-ink" : "text-ink-faint"
              }`}
            >
              {sessionLabel(session)}
            </div>
            <div className="num text-2xs text-ink-faint">{localTime(session.date_start)}</div>
          </button>
        );
      })}
    </div>
  );
}
