"use client";

import { useState } from "react";

import { DriverLapPicker } from "@/components/DriverLapPicker";
import { SessionPicker, useSelection } from "@/components/SessionPicker";
import { Empty, ErrorPanel, Loading, Note, Panel } from "@/components/ui";
import { ApiError, api, type EngineerResponse } from "@/lib/api";

const SUGGESTIONS = [
  "Where did driver A lose the most time to driver B?",
  "Which corners show the largest braking-point difference?",
  "Is the time loss mostly on corner entry, minimum speed, or exit?",
  "Which driver had the more consistent long-run pace?",
  "How did tyre age correlate with lap time?",
  "Which strategy scenarios are worth investigating?",
];

export default function EngineerPage() {
  const [selection] = useSelection();
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<EngineerResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);

  const ready = selection.sessionKey != null && question.trim().length > 2;

  async function submit() {
    if (!ready) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.ask({
        session_key: selection.sessionKey as number,
        question: question.trim(),
        driver_a: selection.driverA,
        driver_b: selection.driverB,
        lap_a: selection.lapA,
        lap_b: selection.lapB,
      });
      setResult(response);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3">
      <Panel title="Context" bodyClassName="space-y-3 p-3">
        <SessionPicker compact />
        <DriverLapPicker />
        <Note>
          The assistant is given only the metrics computed from this session: lap deltas, corner
          measurements, stint fits and their uncertainties. It is instructed that every number it
          states must appear in that evidence, and that the right answer to an unsupported
          question is to say so. It has no access to general Formula 1 knowledge about this race.
        </Note>
      </Panel>

      <Panel title="Question" bodyClassName="space-y-3 p-3">
        <textarea
          className="field min-h-[80px] resize-y"
          placeholder="Ask about this session…"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit();
          }}
        />
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="rounded border border-graphite-700 bg-graphite-850 px-2 py-1 text-2xs text-ink-muted transition-colors hover:border-graphite-500 hover:text-ink"
              onClick={() => setQuestion(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn btn-primary"
            disabled={!ready || loading}
            onClick={submit}
          >
            {loading ? "Analysing…" : "Ask"}
          </button>
          <span className="text-2xs text-ink-faint">⌘/Ctrl + Enter</span>
        </div>
      </Panel>

      {loading && (
        <Panel bodyClassName="p-0">
          <Loading label="Computing the evidence bundle" />
        </Panel>
      )}

      {error && (
        <Panel bodyClassName="p-0">
          <ErrorPanel error={error} />
        </Panel>
      )}

      {result && (
        <>
          <Panel
            title="Analysis"
            right={
              result.model ? (
                <span className="num text-2xs text-ink-faint">{result.model}</span>
              ) : null
            }
            bodyClassName="p-3 space-y-3"
          >
            {result.answer ? (
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink">
                {result.answer}
              </div>
            ) : (
              <Empty
                title="No narrative generated"
                detail={result.reason ?? "The evidence bundle below is still complete and usable."}
              />
            )}
            {result.disclaimer && <Note>{result.disclaimer}</Note>}
          </Panel>

          <Panel
            title="Evidence"
            right={
              <button
                type="button"
                className="btn"
                onClick={() => setShowEvidence((value) => !value)}
              >
                {showEvidence ? "Hide" : "Show"} raw bundle
              </button>
            }
            bodyClassName="p-3 space-y-2"
          >
            <p className="text-2xs leading-relaxed text-ink-faint">
              Everything the assistant was given. Any figure in the analysis above that does not
              appear here is an error, and should be reported as one.
            </p>
            {showEvidence && (
              <pre className="num max-h-[520px] overflow-auto rounded border border-graphite-700 bg-graphite-950 p-3 text-2xs leading-relaxed text-ink-muted">
                {JSON.stringify(result.evidence, null, 2)}
              </pre>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
