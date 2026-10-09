"use client";

import { useEffect, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import * as echarts from "echarts";

import type { FeedStatus, Provenance } from "@/lib/api";

/* ------------------------------------------------------------------ */
/* Panels                                                              */
/* ------------------------------------------------------------------ */

export function Panel({
  title,
  right,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`panel flex min-h-0 flex-col ${className}`}>
      {title && (
        <header className="panel-header">
          <h2 className="panel-title">{title}</h2>
          {right}
        </header>
      )}
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Provenance                                                          */
/* ------------------------------------------------------------------ */

const PROVENANCE_STYLE: Record<Provenance, { label: string; className: string; hint: string }> = {
  measured: {
    label: "MEASURED",
    className: "border-signal-ok/30 bg-signal-ok/10 text-signal-ok",
    hint: "Reported directly by the timing feed.",
  },
  calculated: {
    label: "CALCULATED",
    className: "border-signal-info/30 bg-signal-info/10 text-signal-info",
    hint: "Arithmetic over measured values. No modelling assumptions.",
  },
  estimated: {
    label: "ESTIMATED",
    className: "border-signal-warn/30 bg-signal-warn/10 text-signal-warn",
    hint: "Depends on a model with stated assumptions. Not a measurement.",
  },
  unavailable: {
    label: "NO DATA",
    className: "border-graphite-600 bg-graphite-800 text-ink-faint",
    hint: "The dataset does not support this value.",
  },
};

export function ProvenanceBadge({
  kind,
  children,
}: {
  kind: Provenance;
  children?: React.ReactNode;
}) {
  const style = PROVENANCE_STYLE[kind];
  return (
    <span
      title={style.hint}
      className={`inline-flex items-center rounded-sm border px-1.5 py-0.5 text-2xs font-semibold tracking-wider ${style.className}`}
    >
      {children ?? style.label}
    </span>
  );
}

export function ConfidenceBadge({ level }: { level: string }) {
  const map: Record<string, string> = {
    high: "border-signal-ok/30 bg-signal-ok/10 text-signal-ok",
    medium: "border-signal-warn/30 bg-signal-warn/10 text-signal-warn",
    low: "border-signal-alert/30 bg-signal-alert/10 text-signal-alert",
    none: "border-graphite-600 bg-graphite-800 text-ink-faint",
  };
  return (
    <span
      className={`inline-flex rounded-sm border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider ${
        map[level] ?? map.none
      }`}
    >
      {level}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Feed status                                                         */
/* ------------------------------------------------------------------ */

/**
 * The one component in the application that is allowed to say "LIVE".
 *
 * It says so only when the backend reports mode === "live", which in turn
 * requires both a live credential and a session actually inside the provider's
 * live window. Archived data is always labelled as archived.
 */
export function FeedStatusBar({ status }: { status: FeedStatus | null }) {
  if (!status) {
    return (
      <div className="flex items-center gap-2 text-2xs text-ink-faint">
        <span className="h-1.5 w-1.5 rounded-full bg-graphite-500" />
        NO SESSION SELECTED
      </div>
    );
  }

  const config = {
    live: { dot: "bg-signal-alert animate-pulse-dot", text: "text-signal-alert", label: "LIVE" },
    historical: { dot: "bg-signal-warn", text: "text-signal-warn", label: "HISTORICAL" },
    archive: { dot: "bg-signal-info", text: "text-signal-info", label: "ARCHIVE" },
  }[status.mode];

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${config.dot}`} />
        <span className={`text-2xs font-bold tracking-[0.16em] ${config.text}`}>
          {config.label}
        </span>
      </div>
      <span className="text-2xs uppercase tracking-wider text-ink-faint">
        {status.provider} · {status.tier} tier
      </span>
      {status.stale && (
        <span className="rounded-sm border border-signal-alert/40 bg-signal-alert/10 px-1.5 py-0.5 text-2xs font-semibold text-signal-alert">
          STALE · LAST UPDATE {Math.round(status.age_seconds ?? 0)}s AGO
        </span>
      )}
      {status.degraded_reason && (
        <span className="text-2xs text-signal-warn">{status.degraded_reason}</span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* States                                                              */
/* ------------------------------------------------------------------ */

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-[120px] items-center justify-center gap-2 text-xs text-ink-faint">
      <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-signal-info" />
      {label}
      <span className="sr-only">Please wait</span>
    </div>
  );
}

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-1 p-6 text-center">
      <p className="text-xs font-medium text-ink-muted">{title}</p>
      {detail && <p className="max-w-md text-2xs leading-relaxed text-ink-faint">{detail}</p>}
    </div>
  );
}

/**
 * Errors are shown with the backend's own wording, which explains what to do
 * (narrow the selection, wait out a rate limit, start the server).
 */
export function ErrorPanel({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="flex h-full min-h-[120px] flex-col items-start justify-center gap-2 p-4">
      <div className="flex items-center gap-2">
        <span className="h-1.5 w-1.5 rounded-full bg-signal-alert" />
        <span className="text-2xs font-bold uppercase tracking-[0.16em] text-signal-alert">
          Request failed
        </span>
      </div>
      <p className="max-w-2xl text-xs leading-relaxed text-ink-muted">{error.message}</p>
      {onRetry && (
        <button type="button" className="btn mt-1" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Note({ children }: { children: React.ReactNode }) {
  return (
    <p className="border-l-2 border-graphite-600 pl-2 text-2xs leading-relaxed text-ink-faint">
      {children}
    </p>
  );
}

export function NoteList({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <ul className="space-y-1">
      {notes.map((note, index) => (
        <li key={index}>
          <Note>{note}</Note>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ */
/* Charts                                                              */
/* ------------------------------------------------------------------ */

export const CHART_BASE: EChartsOption = {
  backgroundColor: "transparent",
  animation: false,
  grid: { left: 54, right: 16, top: 24, bottom: 28, containLabel: false },
  textStyle: { color: "#8d97a3", fontFamily: "var(--font-mono)", fontSize: 10 },
  tooltip: {
    trigger: "axis",
    backgroundColor: "#101317",
    borderColor: "#2a323b",
    borderWidth: 1,
    textStyle: { color: "#e9edf2", fontSize: 11 },
    axisPointer: { type: "line", lineStyle: { color: "#3a444f", type: "dashed" } },
  },
};

export function axis(name?: string, extra: Record<string, unknown> = {}) {
  return {
    type: "value" as const,
    name,
    nameTextStyle: { color: "#5d6670", fontSize: 9, padding: [0, 0, 0, 0] },
    axisLine: { lineStyle: { color: "#2a323b" } },
    axisTick: { show: false },
    axisLabel: { color: "#5d6670", fontSize: 9 },
    splitLine: { lineStyle: { color: "#161a1f" } },
    ...extra,
  };
}

/**
 * Thin wrapper over ECharts.
 *
 * `group` opts the chart into a connected group so that hovering one trace
 * moves the cursor on every other chart showing the same lap — the
 * synchronised cursor the telemetry laboratory needs.
 */
export function Chart({
  option,
  height = 200,
  group,
  onAxisHover,
  className = "",
}: {
  option: EChartsOption;
  height?: number;
  group?: string;
  onAxisHover?: (value: number | null) => void;
  className?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current, undefined, { renderer: "canvas" });
    instance.current = chart;
    if (group) {
      chart.group = group;
      echarts.connect(group);
    }
    const resize = () => chart.resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      instance.current = null;
    };
  }, [group]);

  useEffect(() => {
    instance.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const chart = instance.current;
    if (!chart || !onAxisHover) return;
    const move = (params: unknown) => {
      const event = params as { offsetX?: number };
      if (event.offsetX == null) return;
      const point = chart.convertFromPixel({ seriesIndex: 0 }, [event.offsetX, 0]);
      onAxisHover(Array.isArray(point) ? point[0] : null);
    };
    const leave = () => onAxisHover(null);
    chart.getZr().on("mousemove", move);
    chart.getZr().on("globalout", leave);
    return () => {
      chart.getZr().off("mousemove", move);
      chart.getZr().off("globalout", leave);
    };
  }, [onAxisHover]);

  return <div ref={container} style={{ height }} className={`w-full ${className}`} />;
}

/* ------------------------------------------------------------------ */
/* Data fetching                                                       */
/* ------------------------------------------------------------------ */

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Minimal fetch hook with a reload trigger.
 *
 * `enabled` exists so a page can hold off until the user has chosen a session,
 * rather than firing a request it knows will 422.
 */
export function useAsync<T>(
  factory: () => Promise<T>,
  deps: unknown[],
  enabled = true,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    factory()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err);
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
