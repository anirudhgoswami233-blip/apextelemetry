/**
 * Typed client for the ApexTelemetry backend.
 *
 * Two things this module enforces that are easy to lose elsewhere:
 *
 * 1. Provider errors arrive with a human-readable `detail`. We surface it
 *    verbatim instead of replacing it with "Something went wrong", because the
 *    backend's messages say what to actually do (narrow the selection, wait for
 *    a rate limit, add a key).
 * 2. Numeric channels can legitimately be `null`. Nothing here coerces a null
 *    to zero. A missing measurement must stay missing all the way to the pixel.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter?: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Cannot reach the ApexTelemetry backend at ${API_BASE}. Start it with ` +
        `"uvicorn app.main:app --port 8000" in the backend directory.`,
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let retryAfter: number | null = null;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
      if (body?.retry_after) retryAfter = body.retry_after;
    } catch {
      /* keep the status line */
    }
    throw new ApiError(detail, response.status, retryAfter);
  }
  return (await response.json()) as T;
}

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

export type Provenance = "measured" | "calculated" | "estimated" | "unavailable";

export interface Meeting {
  meeting_key: number;
  meeting_name?: string | null;
  meeting_official_name?: string | null;
  location?: string | null;
  country_name?: string | null;
  circuit_short_name?: string | null;
  date_start?: string | null;
  year?: number | null;
}

export interface SessionInfo {
  session_key: number;
  meeting_key?: number | null;
  session_name?: string | null;
  session_type?: string | null;
  date_start?: string | null;
  date_end?: string | null;
  location?: string | null;
  country_name?: string | null;
  circuit_short_name?: string | null;
  year?: number | null;
  completed?: boolean;
}

export interface DriverInfo {
  driver_number: number;
  full_name?: string | null;
  name_acronym?: string | null;
  team_name?: string | null;
  team_colour?: string | null;
}

export interface FeedStatus {
  provider: string;
  tier: string;
  mode: "live" | "historical" | "archive";
  live_access: boolean;
  session_key?: number | null;
  last_updated?: string | null;
  age_seconds?: number | null;
  stale: boolean;
  stale_threshold_seconds: number;
  degraded_reason?: string | null;
}

export interface TimingRow {
  driver_number: number;
  acronym: string;
  full_name?: string | null;
  team_name?: string | null;
  team_colour?: string | null;
  position?: number | null;
  laps_completed: number;
  last_lap?: number | null;
  best_lap?: number | null;
  is_session_best: boolean;
  sectors: (number | null)[];
  best_sectors: (number | null)[];
  speed_trap?: number | null;
  gap_to_leader?: number | string | null;
  interval?: number | string | null;
  compound?: string | null;
  stint_number?: number | null;
  tyre_age?: number | null;
  tyre_age_provenance: Provenance;
  in_pit: boolean;
  retired: boolean;
}

export interface RaceControlMessage {
  date?: string | null;
  category?: string | null;
  flag?: string | null;
  scope?: string | null;
  lap_number?: number | null;
  driver_number?: number | null;
  message?: string | null;
}

export interface WeatherReading {
  date?: string | null;
  air_temperature?: number | null;
  track_temperature?: number | null;
  humidity?: number | null;
  rainfall?: number | null;
  wind_speed?: number | null;
  wind_direction?: number | null;
}

export interface TimingResponse {
  status: FeedStatus;
  drivers: TimingRow[];
  race_control: RaceControlMessage[];
  weather: WeatherReading | null;
  session_best: number | null;
}

export interface LapInfo {
  driver_number: number;
  lap_number?: number | null;
  lap_duration?: number | null;
  duration_sector_1?: number | null;
  duration_sector_2?: number | null;
  duration_sector_3?: number | null;
  st_speed?: number | null;
  is_pit_out_lap?: boolean | null;
  is_valid: boolean;
  flags: string[];
}

export interface TracePayload {
  driver_number: number;
  lap_number: number | null;
  lap_duration: number | null;
  length_m: number;
  sample_count: number;
  sample_rate_hz: number | null;
  spatial_resolution_m: number | null;
  decimated_by: number;
  channels: {
    distance: (number | null)[];
    time: (number | null)[];
    speed: (number | null)[];
    throttle: (number | null)[];
    brake: (number | null)[];
    rpm: (number | null)[];
    gear: (number | null)[];
    drs: (number | null)[];
    x: (number | null)[] | null;
    y: (number | null)[] | null;
  };
  warnings: string[];
  provenance: Record<string, string>;
}

export interface DeltaRegion {
  start_m: number;
  end_m: number;
  delta_change_s: number;
  peak_speed_difference_kph: number | null;
}

export interface SectorDelta {
  sector: number;
  a_seconds: number | null;
  b_seconds: number | null;
  delta_seconds: number | null;
  provenance: Provenance;
}

export interface LapDelta {
  distance: (number | null)[];
  delta: (number | null)[];
  speed_difference: (number | null)[];
  driver_a: number;
  driver_b: number;
  lap_a: number | null;
  lap_b: number | null;
  final_delta_s: number;
  max_delta_s: number;
  min_delta_s: number;
  regions: DeltaRegion[];
  sectors: SectorDelta[];
  alignment_mode: string;
  length_a_m: number;
  length_b_m: number;
  spatial_resolution_m: number | null;
  notes: string[];
}

export interface CompareResponse {
  delta: LapDelta;
  a: TracePayload;
  b: TracePayload;
}

export interface Corner {
  number: number;
  entry_m: number;
  apex_m: number;
  exit_m: number;
  direction: string;
  min_radius_m: number | null;
  peak_curvature: number;
  source: string;
  apex_provenance: Provenance;
  notes: string[];
}

export interface CircuitMapResponse {
  circuit: {
    corners: Corner[];
    x: (number | null)[] | null;
    y: (number | null)[] | null;
    distance: (number | null)[];
    curvature: (number | null)[] | null;
    scale_factor: number | null;
    lap_length_m: number;
    reference_driver: number;
    reference_lap: number | null;
    config: Record<string, number>;
    method: string;
    notes: string[];
  };
  reference_trace: TracePayload;
}

export interface BrakeEvent {
  onset_distance_m: number;
  onset_uncertainty_m: number;
  onset_time_s: number;
  speed_at_onset_kph: number | null;
  min_speed_kph: number | null;
  duration_s: number | null;
  method: string;
  provenance: Provenance;
  confidence: "high" | "medium" | "low" | "none";
  notes: string[];
}

export interface CornerMetrics {
  corner: number;
  driver_number: number;
  lap_number: number | null;
  brake: BrakeEvent | null;
  entry_speed_kph: number | null;
  min_speed_kph: number | null;
  exit_speed_kph: number | null;
  throttle_application_m: number | null;
  time_to_full_throttle_s: number | null;
  gear_at_apex: number | null;
  gear_range: [number, number] | null;
  drs_open: boolean | null;
  elapsed_through_window_s: number | null;
  spatial_resolution_m: number;
  notes: string[];
}

export interface CornerComparison {
  corner: number;
  a: CornerMetrics;
  b: CornerMetrics;
  brake_point_difference_m: number | null;
  brake_point_uncertainty_m: number | null;
  brake_difference_significant: boolean;
  entry_speed_difference_kph: number | null;
  min_speed_difference_kph: number | null;
  exit_speed_difference_kph: number | null;
  time_delta_s: number | null;
  confidence: string;
  interpretation: string;
  notes: string[];
}

export interface CornerAnalysisResponse {
  circuit: CircuitMapResponse["circuit"];
  comparison: CornerComparison[];
  method: {
    corner_detection: Record<string, number | string>;
    brake_detection: Record<string, number | string>;
    explanation: Record<string, string>;
  };
}

export interface DegradationFit {
  slope_s_per_lap: number | null;
  slope_stderr: number | null;
  r_squared: number | null;
  sample_size: number;
  excluded_laps: number[];
  fuel_corrected: boolean;
  provenance: Provenance;
  notes: string[];
}

export interface StintSummary {
  driver_number: number;
  stint_number: number | null;
  compound: string | null;
  lap_start: number | null;
  lap_end: number | null;
  tyre_age_at_start: number | null;
  laps_completed: number;
  lap_times: [number, number, number][];
  median_s: number | null;
  best_s: number | null;
  degradation: DegradationFit | null;
  notes: string[];
}

export interface StrategyResponse {
  stints: StintSummary[];
  pit_stops: {
    driver_number: number;
    stops: number;
    durations_s: number[];
    best_s: number | null;
    median_s: number | null;
    note: string;
  }[];
  neutralised_laps: number[];
  assumptions: Record<string, number>;
  provenance: Record<string, string>;
}

export interface ScenarioTerm {
  label: string;
  seconds: number;
  basis: string;
}

export interface ScenarioResult {
  name: string;
  net_seconds: number;
  terms: ScenarioTerm[];
  assumptions: Record<string, number>;
  provenance: Provenance;
  caveat: string;
}

export interface ProviderDescriptor {
  name: string;
  display_name: string;
  raw_telemetry: boolean;
  car_location: boolean;
  live: boolean;
  streaming: boolean;
  first_season: number | null;
  last_season: number | null;
  has_key: boolean;
  attribution: string;
  licence_note: string;
  enabled: boolean;
  unsupported_workspaces: string[];
}

export interface EngineerResponse {
  answer: string | null;
  evidence: Record<string, unknown>;
  narration: "generated" | "unavailable" | "failed";
  reason?: string;
  model?: string;
  disclaimer?: string;
}

/* ------------------------------------------------------------------ */
/* Endpoints                                                           */
/* ------------------------------------------------------------------ */

export const api = {
  health: () => request<{ status: string; provider: string; live_access: boolean }>("/api/health"),

  providers: () => request<{ providers: ProviderDescriptor[] }>("/api/providers"),

  seasons: () => request<{ seasons: number[]; note: string }>("/api/seasons"),

  meetings: (year: number) => request<{ meetings: Meeting[] }>(`/api/meetings?year=${year}`),

  sessions: (params: { year?: number; meetingKey?: number }) => {
    const query = new URLSearchParams();
    if (params.year) query.set("year", String(params.year));
    if (params.meetingKey) query.set("meeting_key", String(params.meetingKey));
    return request<{ sessions: SessionInfo[] }>(`/api/sessions?${query}`);
  },

  weekend: (meetingKey: number) =>
    request<{ meeting_key: number; sessions: SessionInfo[] }>(`/api/weekend/${meetingKey}`),

  status: (sessionKey: number) => request<FeedStatus>(`/api/session/${sessionKey}/status`),

  drivers: (sessionKey: number) =>
    request<{ drivers: DriverInfo[] }>(`/api/session/${sessionKey}/drivers`),

  laps: (sessionKey: number, driverNumber?: number) => {
    const query = driverNumber ? `?driver_number=${driverNumber}` : "";
    return request<{ laps: LapInfo[] }>(`/api/session/${sessionKey}/laps${query}`);
  },

  timing: (sessionKey: number) => request<TimingResponse>(`/api/session/${sessionKey}/timing`),

  telemetry: (sessionKey: number, driverNumber: number, lapNumber?: number, decimate = 1) => {
    const query = new URLSearchParams({
      driver_number: String(driverNumber),
      decimate: String(decimate),
    });
    if (lapNumber != null) query.set("lap_number", String(lapNumber));
    return request<TracePayload>(`/api/session/${sessionKey}/telemetry?${query}`);
  },

  compare: (args: {
    sessionKey: number;
    driverA: number;
    driverB: number;
    lapA?: number | null;
    lapB?: number | null;
    mode?: "truncate" | "normalize";
  }) => {
    const query = new URLSearchParams({
      driver_a: String(args.driverA),
      driver_b: String(args.driverB),
      mode: args.mode ?? "truncate",
    });
    if (args.lapA != null) query.set("lap_a", String(args.lapA));
    if (args.lapB != null) query.set("lap_b", String(args.lapB));
    return request<CompareResponse>(`/api/session/${args.sessionKey}/compare?${query}`);
  },

  compareCsvUrl: (args: {
    sessionKey: number;
    driverA: number;
    driverB: number;
    lapA?: number | null;
    lapB?: number | null;
    mode?: "truncate" | "normalize";
  }) => {
    const query = new URLSearchParams({
      driver_a: String(args.driverA),
      driver_b: String(args.driverB),
      mode: args.mode ?? "truncate",
    });
    if (args.lapA != null) query.set("lap_a", String(args.lapA));
    if (args.lapB != null) query.set("lap_b", String(args.lapB));
    return `${API_BASE}/api/session/${args.sessionKey}/compare.csv?${query}`;
  },

  circuit: (sessionKey: number, referenceDriver?: number, radiusThreshold = 300) => {
    const query = new URLSearchParams({ radius_threshold_m: String(radiusThreshold) });
    if (referenceDriver) query.set("reference_driver", String(referenceDriver));
    return request<CircuitMapResponse>(`/api/session/${sessionKey}/circuit?${query}`);
  },

  corners: (args: {
    sessionKey: number;
    driverA: number;
    driverB: number;
    lapA?: number | null;
    lapB?: number | null;
    radiusThreshold?: number;
    brakeMethod?: "auto" | "flag" | "deceleration";
    decelThreshold?: number;
  }) => {
    const query = new URLSearchParams({
      driver_a: String(args.driverA),
      driver_b: String(args.driverB),
      radius_threshold_m: String(args.radiusThreshold ?? 300),
      brake_method: args.brakeMethod ?? "auto",
      decel_threshold_mps2: String(args.decelThreshold ?? -3),
    });
    if (args.lapA != null) query.set("lap_a", String(args.lapA));
    if (args.lapB != null) query.set("lap_b", String(args.lapB));
    return request<CornerAnalysisResponse>(`/api/session/${args.sessionKey}/corners?${query}`);
  },

  undercut: (args: {
    freshTyreGainPerLap: number;
    lapsUntilRivalPits: number;
    degradationPerLap?: number | null;
    extraLapsOnOldTyreAtEnd?: number;
  }) => {
    const query = new URLSearchParams({
      fresh_tyre_gain_s_per_lap: String(args.freshTyreGainPerLap),
      laps_until_rival_pits: String(args.lapsUntilRivalPits),
      extra_laps_on_old_tyre_at_end: String(args.extraLapsOnOldTyreAtEnd ?? 0),
    });
    if (args.degradationPerLap != null) {
      query.set("degradation_s_per_lap", String(args.degradationPerLap));
    }
    return request<ScenarioResult>(`/api/scenario/undercut?${query}`);
  },

  safetyCar: (args: {
    lapsRemaining: number;
    currentTyreAge: number;
    degradationPerLap?: number | null;
  }) => {
    const query = new URLSearchParams({
      laps_remaining: String(args.lapsRemaining),
      current_tyre_age: String(args.currentTyreAge),
    });
    if (args.degradationPerLap != null) {
      query.set("degradation_s_per_lap", String(args.degradationPerLap));
    }
    return request<ScenarioResult>(`/api/scenario/safety-car?${query}`);
  },

  strategy: (sessionKey: number, fuelEffect = 0.055) =>
    request<StrategyResponse>(
      `/api/session/${sessionKey}/strategy?fuel_effect_s_per_lap=${fuelEffect}`,
    ),

  ask: (body: {
    session_key: number;
    question: string;
    driver_a?: number | null;
    driver_b?: number | null;
    lap_a?: number | null;
    lap_b?: number | null;
  }) =>
    request<EngineerResponse>("/api/engineer/ask", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
