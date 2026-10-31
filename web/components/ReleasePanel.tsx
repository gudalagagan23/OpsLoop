import { ago, type ReleaseInfo, type RolloutEvent } from "@/lib/api";
import { Icon } from "./Icon";

const STAGES = [5, 25, 100];
const VERDICT_CLASS: Record<string, string> = {
  RESOLVED: "ok",
  PARTIAL: "warn",
  INCONCLUSIVE: "warn",
  UNSUCCESSFUL: "bad",
};
const METRIC_LABEL: Record<string, string> = {
  error_rate: "Error rate",
  create_error_rate: "Task-creation errors",
  list_p95_ms: "Task list p95",
  p95_ms: "p95 latency",
};

export function fmtMetric(metric: string, v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (metric.includes("error_rate")) return `${(v * 100).toFixed(v < 0.1 ? 2 : 1)}%`;
  return `${v.toFixed(0)} ms`;
}

export function eventText(e: RolloutEvent): string {
  switch (e.event) {
    case "candidate_ready":
      return "Candidate container is up and healthy";
    case "stage_started":
      return `${e.pct}% of traffic moved to the fix`;
    case "stage_passed":
      return `${e.pct}% passed: ${e.candidate_n ?? 0} requests, ${fmtMetric("error_rate", e.candidate_error_rate)} errors, p95 ${fmtMetric("p95_ms", e.candidate_p95_ms)}`;
    case "rolled_back":
      return `Rolled back automatically at ${e.pct}%${e.reasons?.length ? `: ${e.reasons.join("; ")}` : ""}`;
    case "promoted":
      return "Promoted to 100% (simulated: stable switched the planted fault off)";
    default:
      return e.event.replaceAll("_", " ");
  }
}

/** Staged rollout progress and the Judge's verdict for one incident. */
export function ReleasePanel({ release, state }: { release: ReleaseInfo; state: string }) {
  const dep = release.deployments.at(-1);
  const out = release.outcome;
  if (!dep && !out) return null;
  const live = state === "DEPLOYING" || state === "VERIFYING";
  return (
    <section className="panel">
      <h2>
        <Icon name="rocket" /> Release &amp; verdict
        {live && (
          <span className="pill warn live">{state === "DEPLOYING" ? "rolling out" : "verifying"}</span>
        )}
        {out && <span className={`pill ${VERDICT_CLASS[out.verdict] ?? ""}`}>Judge: {out.verdict}</span>}
      </h2>
      {dep && (
        <>
          <div className="rollout-head">
            <span className="mono">{dep.version_hash.slice(0, 12)}</span>
            <span className={`pill ${dep.state === "completed" ? "ok" : dep.state === "rolled_back" ? "bad" : "warn"}`}>
              {dep.state.replaceAll("_", " ")}
            </span>
            <span className="muted small">started {ago(dep.started_at)} ago</span>
          </div>
          <div
            className={`rollout-bar big ${dep.state === "rolled_back" ? "bad" : dep.state === "completed" ? "done" : "running"}`}
            role="progressbar"
            aria-valuenow={dep.stage_pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Share of traffic on the fix"
          >
            <span style={{ width: `${dep.stage_pct}%` }} />
            {STAGES.map((s) => (
              <i key={s} style={{ left: `${s}%` }} className={dep.stage_pct >= s ? "reached" : ""}>
                <b>{s}%</b>
              </i>
            ))}
          </div>
          <ol className="rollout-events">
            {dep.events.map((e, i) => (
              <li key={i} className={`ev-${e.event}`}>
                <span className="muted small">{new Date(e.t * 1000).toLocaleTimeString()}</span> {eventText(e)}
              </li>
            ))}
          </ol>
        </>
      )}
      {out && (
        <div className={`verdict-box v-${VERDICT_CLASS[out.verdict] ?? "info"}`}>
          <div className="verdict-title">
            <Icon name="scale" size={22} />
            <strong>{out.verdict}</strong>
            <span className="muted small">
              {out.mode === "before_after" ? "before / after" : "candidate vs control, concurrently"} ·{" "}
              {out.summary.candidate_samples ?? 0} measured requests on the fix
            </span>
          </div>
          {out.explanation && <p>{out.explanation}</p>}
          {out.summary.target && (
            <p className="muted small">
              Target: {METRIC_LABEL[out.summary.target.metric] ?? out.summary.target.metric} {out.summary.target.op}{" "}
              {fmtMetric(out.summary.target.metric, out.summary.target.value)}
            </p>
          )}
          {out.summary.effects && out.summary.effects.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Control</th>
                    <th>Fix</th>
                    <th>95% CI of improvement</th>
                  </tr>
                </thead>
                <tbody>
                  {out.summary.effects.map((e) => (
                    <tr key={e.metric}>
                      <td>{METRIC_LABEL[e.metric] ?? e.metric}</td>
                      <td>{fmtMetric(e.metric, e.control)}</td>
                      <td>{fmtMetric(e.metric, e.candidate)}</td>
                      <td className="mono">
                        {e.ci_low === null || e.ci_high === null
                          ? "–"
                          : `[${fmtMetric(e.metric, e.ci_low)}, ${fmtMetric(e.metric, e.ci_high)}]`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
