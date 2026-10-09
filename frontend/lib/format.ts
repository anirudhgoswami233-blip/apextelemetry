/**
 * Formatting helpers.
 *
 * The rule throughout: a missing value renders as an em dash, never as 0,
 * "0.000", or "—" standing in for a real zero. An engineer must be able to
 * tell "we did not measure this" apart from "this measured zero" at a glance.
 */

export const DASH = "—";

export function lapTime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return DASH;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  if (minutes === 0) return rest.toFixed(3);
  return `${minutes}:${rest.toFixed(3).padStart(6, "0")}`;
}

export function sectorTime(seconds: number | null | undefined): string {
  return seconds == null || !Number.isFinite(seconds) ? DASH : seconds.toFixed(3);
}

export function delta(seconds: number | null | undefined, digits = 3): string {
  if (seconds == null || !Number.isFinite(seconds)) return DASH;
  const sign = seconds > 0 ? "+" : seconds < 0 ? "−" : "";
  return `${sign}${Math.abs(seconds).toFixed(digits)}`;
}

export function metres(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? DASH : `${value.toFixed(digits)} m`;
}

export function speed(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? DASH : `${value.toFixed(digits)}`;
}

export function signedSpeed(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(digits)}`;
}

/** A measured distance with its uncertainty, e.g. "318 ±12 m". */
export function withUncertainty(
  value: number | null | undefined,
  uncertainty: number | null | undefined,
  unit = "m",
): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  if (uncertainty == null || !Number.isFinite(uncertainty)) return `${value.toFixed(0)} ${unit}`;
  return `${value.toFixed(0)} ±${uncertainty.toFixed(0)} ${unit}`;
}

export function gap(value: number | string | null | undefined): string {
  if (value == null) return DASH;
  if (typeof value === "string") return value; // the feed sends "+1 LAP" and similar
  return value === 0 ? "LEADER" : `+${value.toFixed(3)}`;
}

const COMPOUND_COLOURS: Record<string, string> = {
  SOFT: "#ff4d5e",
  MEDIUM: "#ffb020",
  HARD: "#e9edf2",
  INTERMEDIATE: "#2ee6a8",
  WET: "#4da6ff",
};

export function compoundColour(compound: string | null | undefined): string {
  if (!compound) return "#5d6670";
  return COMPOUND_COLOURS[compound.toUpperCase()] ?? "#5d6670";
}

export function compoundLetter(compound: string | null | undefined): string {
  if (!compound) return "?";
  const upper = compound.toUpperCase();
  if (upper === "INTERMEDIATE") return "I";
  if (upper === "WET") return "W";
  return upper.charAt(0);
}

export function teamColour(colour: string | null | undefined): string {
  if (!colour) return "#5d6670";
  return colour.startsWith("#") ? colour : `#${colour}`;
}

export function sessionLabel(session: {
  session_name?: string | null;
  session_type?: string | null;
}): string {
  return session.session_name ?? session.session_type ?? "Session";
}

export function localTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DASH;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DASH;
  return date.toLocaleTimeString(undefined, { hour12: false });
}

/** Strip nulls from a chart series without shifting the x-axis. */
export function pairs(
  xs: (number | null)[],
  ys: (number | null)[],
): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i < Math.min(xs.length, ys.length); i += 1) {
    const x = xs[i];
    const y = ys[i];
    if (x != null && y != null && Number.isFinite(x) && Number.isFinite(y)) {
      out.push([x, y]);
    }
  }
  return out;
}
