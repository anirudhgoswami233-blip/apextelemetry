# ApexTelemetry

**🔴 Live: [apextelemetry-seven.vercel.app](https://apextelemetry-seven.vercel.app)**

> Hosted on a free tier — the backend sleeps after ~15 minutes idle, so the first
> load may take 30–60 seconds to wake. Subsequent loads are instant.

A Formula 1 telemetry, lap-delta, corner and strategy analysis workstation, built
on real OpenF1 data from 2023 onward.

**What it does:** synchronised telemetry comparison between any two drivers on any
two laps · cumulative lap-delta on a distance axis · corner-by-corner braking
analysis with a configurable detection threshold · tyre degradation fits with
stated uncertainty · an AI race engineer grounded in computed evidence rather
than general F1 knowledge.

**The idea behind it:** every number on screen says where it came from. The
provider measures lap times, speeds and positions. It does not measure tyre
condition, fuel load, apex location, or the exact metre a driver touched the
brakes. Anything in the second category is computed, labelled `calculated` or
`estimated`, and carries its own uncertainty — a braking point reads `318 ±12 m`,
and a difference between two drivers is only called significant when it exceeds
the combined sampling uncertainty of both traces.

**Stack:** FastAPI · numpy/scipy · Next.js · TypeScript · ECharts · Tailwind
**Tests:** 62, run against a synthetic circuit with known ground truth

---

## Quick start

```bash
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 2. Frontend, in a second terminal
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open http://localhost:3000. Interactive API docs are at http://localhost:8000/docs.

**No API key is required.** OpenF1's Community tier needs no credential and
covers every session from 2023 onward.

---

## Data provider

| | OpenF1 Community | OpenF1 Sponsor |
|---|---|---|
| Credential | none | API key |
| Coverage | 2023 → present, **historical only** | historical + live |
| Rate limits | 3 req/s · 30 req/min | 6 req/s · 60 req/min |
| Live transports | — | REST, MQTT, WebSocket |
| Cost | free | €9.90/month |

OpenF1 treats a session as live from 30 minutes before it starts to 30 minutes
after it ends. Outside that window everything is historical for everybody.

### Turning live on

Set one environment variable. Nothing else changes — not the frontend, not the
analytics, not a single component.

```bash
export OPENF1_API_KEY=...    # backend only; never reaches the browser
```

The backend then reports `mode: "live"` for sessions inside the live window, and
the status bar switches from `HISTORICAL` to `LIVE`. **Without a key the
application will never describe data as live**, even when a session is actually
running — it will say `HISTORICAL` and explain why. That rule is enforced in the
data layer (`SessionService.feed_status`), not in UI copy.

### On "free live F1 telemetry"

There isn't any, and it is worth being clear about why:

- **FastF1** can *record* the official SignalR live-timing feed for free, but it
  explicitly cannot process it in real time — you save the stream and analyse it
  afterwards. It is also pinned to old Python versions. If you want it, it
  belongs in a separate worker, not this backend.
- **Jolpica-F1** (the Ergast successor) is free and needs no key, but serves
  results and standings only — no telemetry, and updated after sessions.
- **T1API** serves pre-computed analyses as PNG and JSON, has no live or
  streaming capability, and cannot support a configurable brake-onset threshold
  because you cannot re-run a detection algorithm against someone else's
  finished chart. It is registered as a provider stub (see below) for its 2018+
  historical coverage, not as a live source.

---

## Architecture

```
backend/
  app/
    config.py              environment and credential handling
    models.py              typed provider payloads; missing stays missing
    cache.py               memory tier for live, disk tier for immutable archives
    providers/
      base.py              TelemetryProvider protocol + capability flags
      openf1.py            OpenF1 adapter: rate limiting, retries, live window
      t1api.py             registered stub, deliberately not implemented
    analytics/             ← pure numpy/scipy, no framework, fully tested
      alignment.py         speed→distance integration, resampling, pair alignment
      delta.py             lap delta, time-swing regions, lap classification
      braking.py           brake-onset detection with explicit uncertainty
      corners.py           circuit geometry from position, corner metrics
      stints.py            degradation regression, pit stops, scenarios
    service.py             orchestration between provider and analytics
    routers/               FastAPI routes
  tests/
    synthetic.py           a constructed circuit with known ground truth
    test_analytics.py      62 tests

frontend/
  app/                     Next.js App Router: timing, telemetry, corners,
                           strategy, race engineer
  components/              panels, charts, circuit map, pickers
  lib/api.ts               typed client
```

The `analytics` package imports no web framework and no HTTP client. That is
what lets it be tested without a network and reused behind any transport.

### Adding a provider

Implement the `TelemetryProvider` protocol and declare honest
`ProviderCapabilities`. The capability flags drive the UI directly: a provider
that reports `raw_telemetry=False` causes the telemetry laboratory and corner
analysis to be disabled rather than silently degraded.

```python
caps = ProviderCapabilities(name="...", raw_telemetry=False, ...)
caps.unsupported_workspaces()   # ['corner-analysis', 'lap-delta', 'telemetry-lab']
```

---

## The methods, and their limits

### Distance

The provider publishes no distance channel. Distance is obtained by trapezoidal
integration of the measured speed channel:

```
d(i) = d(i-1) + ((v(i-1) + v(i)) / 2) · (t(i) - t(i-1))
```

Against the synthetic circuit this recovers the true lap length to within
**0.01%**. It is a *calculated* channel and is labelled as such everywhere.

### Why nothing is precise to the metre

OpenF1 samples car telemetry at about **3.7 Hz**. At 300 km/h a car travels
roughly **22 m between consecutive samples**. No event derived from this data —
a braking point, an apex, a throttle application — can be located more precisely
than about half that gap.

Rather than hide this, every braking point is reported as `318 ±12 m`, and a
difference between two drivers is only called *significant* when it exceeds the
combined uncertainty of both traces. Differences that do not clear that bar are
shown struck through, with a tooltip explaining why.

### Brake-onset detection

Two methods, both configurable from the UI:

- **`flag`** — OpenF1's brake channel is a 0/100 flag, not a pressure trace.
  Onset is the first sample where it goes high and stays high for at least
  `min_duration_s`. Reported as the midpoint between the last unbraked and first
  braked sample, with half that interval as uncertainty. Provenance:
  `calculated`.
- **`deceleration`** — fallback when the brake channel is absent. Speed is
  smoothed and differentiated; onset is where longitudinal deceleration passes
  `decel_threshold_mps2` (default −3.0 m/s², clear of the 1–2 m/s² a car sheds
  to drag alone). Provenance: `estimated`, because a lift or a downshift
  produces the same signature.

The analysis **never concludes that a driver braked too late.** It reports that
one braked later, alongside entry speed, minimum speed, exit speed and the net
time through the corner window, and leaves the judgement to the engineer. There
is a test asserting the interpretation text contains no such verdict.

### Corner detection

Corners are derived, not looked up. The reference lap's racing line is
resampled, smoothed, and differentiated to signed curvature:

```
k = (x'y'' − y'x'') / (x'² + y'²)^(3/2)
```

Any stretch tighter than `radius_threshold_m` is a corner; nearby stretches are
merged and short ones discarded. A corner that straddles the start/finish line
is stitched back into one.

The position feed's units are not documented, so the scale is **recovered**
rather than assumed, by comparing the raw path length against the
speed-integrated distance — two independent measurements of the same lap. The
detected factor is reported in the UI.

Caveats that travel with every result:

- Corner numbers come from one driver's line on one lap, not an official circuit
  map. They will usually agree with the official numbering and will sometimes
  disagree on double-apex corners.
- The **apex is the point of maximum curvature on the chosen line**. It is not
  the geometric apex of the track and it moves with the line taken. It is always
  labelled `estimated`, and where position data is missing no apex is reported
  at all.

### Degradation

A least-squares fit of lap time against tyre age, after removing laps above 105%
of the stint median (traffic and lock-ups have nothing to do with tyre wear) and
every lap race control flagged as neutralised.

Reported with a standard error, R², and sample size — never as a bare number.
A slope smaller than its own standard error is shown as **"no trend"**, not as a
small degradation.

Fuel burn makes lap times fall as a stint goes on, so a raw slope understates
degradation; the corrected slope adds the assumed fuel effect back. That
assumption is adjustable in the UI and echoed in every result.

### Strategy scenarios

An undercut is modelled as a sum of named, signed terms — fresh-tyre advantage,
out-lap warm-up, in-lap compromise, extra laps on the worn set — each with its
basis stated. The arithmetic can be audited or overridden. Scenarios are
`estimated` and carry an explicit caveat that they ignore traffic position and
every other car's response.

### The AI race engineer

The model never sees a question without data. The backend first computes an
evidence bundle — real laps, real deltas, real corner metrics, each with
provenance and uncertainty — and the model's only job is to narrate it. It is
instructed that every figure must appear in the bundle, that it must respect
stated uncertainty, and that the right answer to an unsupported question is to
say so.

The full bundle is returned alongside the answer and rendered in the UI. **Any
figure in the answer that is not in the bundle is a bug.**

Without `ANTHROPIC_API_KEY` the endpoint still returns the evidence bundle. The
evidence is the valuable part; the prose is a convenience.

---

## Tests

```bash
cd backend
python -m unittest discover -s tests -t .     # 62 tests, no network required
# or, with pytest installed:  pytest tests/
```

The suite runs against a **constructed circuit** (`tests/synthetic.py`) — a
rounded rectangle with a physically-shaped speed profile built from cornering,
braking and traction limits, sampled at 3.7 Hz with a 0/100 brake flag and
integer gears, exactly like the real feed.

Real telemetry has no ground truth: nobody knows the exact metre a driver
braked, so a test against real data can only check that the code does not crash.
Here the geometry and the braking points are known, so the detectors can be
checked against the number they were supposed to find.

Covered: distance integration exactness, lap-length recovery, step-channel
resampling (gear 4.3 must never exist), delta sign convention and monotonicity,
brake-onset uncertainty actually bracketing the true onset, corner count and
radius recovery, unit-scale detection, start/finish-line stitching, the
no-verdict rule, degradation slope recovery, noise rejection, and outlier
exclusion.

---

## Configuration

### Backend

| Variable | Default | Purpose |
|---|---|---|
| `OPENF1_API_KEY` | unset | Sponsor tier; unlocks the live window |
| `OPENF1_BASE_URL` | `https://api.openf1.org/v1` | |
| `OPENF1_RPS` / `OPENF1_RPM` | 2.5 / 28 | Kept under the published ceiling |
| `APEX_CACHE_DIR` | `.apex-cache` | Disk cache for completed sessions |
| `APEX_CORS_ORIGINS` | `localhost:3000` | Comma-separated |
| `ANTHROPIC_API_KEY` | unset | Optional, race-engineer narration |

### Frontend

| Variable | Default |
|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://127.0.0.1:8000` |

Credentials are read server-side only and proxied. Nothing authenticated is ever
exposed to browser code.

---

## Performance notes

- **Completed sessions are immutable**, so they are cached to disk gzipped and
  never re-fetched. Only data inside the live window uses the short-lived memory
  tier.
- **Telemetry is never fetched session-wide.** Every car-data read is scoped to
  one driver and one lap's time window. A race across twenty cars is tens of
  millions of samples; the UI needs a few thousand.
- Identical concurrent requests are collapsed by a single-flight guard, so eight
  panels asking for the same poll tick produce one upstream call.
- A dual-window token bucket enforces both the per-second and per-minute ceiling,
  because 429s cost more time than throttling does.

---

## Attribution and licensing

Data from **OpenF1** (openf1.org). ApexTelemetry and OpenF1 are unofficial and
not affiliated with Formula 1, the FIA or Formula One Management.

OpenF1 site content is licensed **CC BY-NC-SA 4.0**. The project describes its
intended use as educational, personal learning, research and non-commercial fan
engagement; attribution is not required but a link back is appreciated.
Commercial use, redistribution of the underlying data, or public display at
scale should be discussed with the project first. The underlying Formula 1
timing data remains the rights-holders' property.

This application caches completed sessions locally for performance. If you
deploy it publicly, review the caching and redistribution posture against the
provider's current terms before doing so.

---

## Known gaps

Stated plainly rather than discovered later:

- **Live streaming is polling.** The WebSocket/MQTT transports require a Sponsor
  key; the poll path is rate-limit-aware and ready to be swapped behind the same
  provider interface.
- **No PostgreSQL yet.** The disk cache covers the session-metadata and
  telemetry-reuse cases the UI actually exercises. Persisting *analysis results*
  — the case that genuinely needs a database — is not implemented.
- **The T1API adapter is a stub** by deliberate choice (see `providers/t1api.py`
  for the reasoning), and its licensing is unresolved.
- **Mini-sector comparison** uses whatever `segments_sector_*` the feed provides;
  the segment encoding is not formally documented, so it is treated as
  indicative.
- **Teammate and season-trend reports** (spec §9) are served by the existing
  comparison endpoints but have no dedicated page yet.
