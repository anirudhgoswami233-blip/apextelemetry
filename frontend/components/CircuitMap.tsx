"use client";

import { useMemo } from "react";

import type { CircuitMapResponse, Corner } from "@/lib/api";

/**
 * The circuit drawn from the reference lap's own position samples.
 *
 * This is not a surveyed track map. It is the line one car took on one lap,
 * which is why corner markers are placed at the point of maximum curvature on
 * *that* line and labelled as approximate everywhere they appear.
 */
export function CircuitMap({
  circuit,
  selected,
  onSelect,
  height = 420,
}: {
  circuit: CircuitMapResponse["circuit"];
  selected: number | null;
  onSelect: (corner: number | null) => void;
  height?: number;
}) {
  const geometry = useMemo(() => {
    const xs = (circuit.x ?? []).filter((v): v is number => v != null);
    // SVG's y axis grows downward while the position feed's grows upward, so
    // the sign is flipped once here. Doing it in the data rather than with a
    // CSS transform keeps text upright without a counter-transform.
    const ys = (circuit.y ?? []).filter((v): v is number => v != null).map((v) => -v);
    if (xs.length < 10 || ys.length < 10 || xs.length !== ys.length) return null;

    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const width = maxX - minX || 1;
    const depth = maxY - minY || 1;
    const pad = Math.max(width, depth) * 0.08;

    const points = xs.map((x, index) => `${x},${ys[index]}`).join(" ");

    const markerFor = (distanceM: number) => {
      const distances = circuit.distance;
      let closest = 0;
      let best = Infinity;
      for (let i = 0; i < distances.length; i += 1) {
        const d = distances[i];
        if (d == null) continue;
        const gap = Math.abs(d - (distanceM % circuit.lap_length_m));
        if (gap < best) {
          best = gap;
          closest = i;
        }
      }
      return { x: xs[closest] ?? minX, y: ys[closest] ?? minY };
    };

    return {
      points,
      viewBox: `${minX - pad} ${minY - pad} ${width + pad * 2} ${depth + pad * 2}`,
      scale: Math.max(width, depth),
      markerFor,
    };
  }, [circuit]);

  if (!geometry) {
    return (
      <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-1 p-6 text-center">
        <p className="text-xs font-medium text-ink-muted">No track geometry available</p>
        <p className="max-w-sm text-2xs leading-relaxed text-ink-faint">
          This session has no usable car-position data, so the circuit cannot be drawn. Corner
          boundaries below were approximated from speed minima instead.
        </p>
      </div>
    );
  }

  const r = geometry.scale * 0.022;

  return (
    <div className="relative" style={{ height }}>
      <svg
        viewBox={geometry.viewBox}
        className="h-full w-full"
        role="img"
        aria-label={`Circuit map with ${circuit.corners.length} detected corners`}
      >
        <polyline
          points={geometry.points}
          fill="none"
          stroke="#2a323b"
          strokeWidth={geometry.scale * 0.014}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <polyline
          points={geometry.points}
          fill="none"
          stroke="#4a5563"
          strokeWidth={geometry.scale * 0.006}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {circuit.corners.map((corner) => {
          const point = geometry.markerFor(corner.apex_m);
          const isSelected = selected === corner.number;
          return (
            <g
              key={corner.number}
              onClick={() => onSelect(isSelected ? null : corner.number)}
              style={{ cursor: "pointer" }}
            >
              <circle
                cx={point.x}
                cy={point.y}
                r={r}
                fill={isSelected ? "#4da6ff" : "#101317"}
                stroke={isSelected ? "#4da6ff" : "#5d6670"}
                strokeWidth={geometry.scale * 0.003}
              />
              <text
                x={point.x}
                y={point.y}
                textAnchor="middle"
                dominantBaseline="central"
                fill={isSelected ? "#07080a" : "#8d97a3"}
                fontSize={r * 1.1}
                fontFamily="var(--font-mono)"
                fontWeight="600"
              >
                {corner.number}
              </text>
            </g>
          );
        })}
      </svg>

      <p className="absolute bottom-1 left-2 text-2xs text-ink-faint">
        Reference: car #{circuit.reference_driver}
        {circuit.reference_lap != null && ` lap ${circuit.reference_lap}`} ·{" "}
        {circuit.method === "curvature-of-racing-line"
          ? "corners from path curvature"
          : "corners approximated from speed"}
      </p>
    </div>
  );
}

export function CornerChips({
  corners,
  selected,
  onSelect,
}: {
  corners: Corner[];
  selected: number | null;
  onSelect: (corner: number | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {corners.map((corner) => (
        <button
          key={corner.number}
          type="button"
          onClick={() => onSelect(selected === corner.number ? null : corner.number)}
          className={`num rounded border px-2 py-1 text-2xs transition-colors ${
            selected === corner.number
              ? "border-signal-info/60 bg-signal-info/15 text-signal-info"
              : "border-graphite-700 bg-graphite-850 text-ink-muted hover:border-graphite-500"
          }`}
          title={
            corner.min_radius_m
              ? `Turn ${corner.number} · ${corner.direction} · ~${corner.min_radius_m.toFixed(0)} m radius`
              : `Turn ${corner.number}`
          }
        >
          T{corner.number}
        </button>
      ))}
    </div>
  );
}
