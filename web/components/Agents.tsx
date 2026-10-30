"use client";

import Link from "next/link";
import { ago, type AgentsInfo, type LlmCallRow, type Overview, type RecentSignal } from "@/lib/api";
import { Skeleton } from "./CommandCenter";
import { Icon } from "./Icon";
import { PipelineFlow } from "./Pipeline";
import { useApi, useNow } from "./useApi";

const COMPONENT_ICON: Record<string, string> = {
  taskflow: "server",
  telemetry: "activity",
  watchers: "eye",
  incident_manager: "board",
  ledger: "ledger",
  knowledge: "book",
  investigator: "brain",
  orchestrator: "cpu",
  specialists: "building",
  sandbox: "flask",
  approval: "shield",
  deploy: "rocket",
  judge: "scale",
  learner: "spark",
};

const STATUS_HELP: Record<string, string> = {
  active: "Running and did work in the last 10 minutes",
  working: "Busy on an incident right now",
  ready: "Running, waiting for work",
  waiting: "Waiting on a person",
  down: "Not responding",
  planned: "Not built yet",
};

export function Agents() {
  const agents = useApi<AgentsInfo>("/agents", 8000);
  const overview = useApi<Overview>("/overview", 10000);
  const calls = useApi<LlmCallRow[]>("/llm/calls?limit=20");
  const signals = useApi<RecentSignal[]>("/signals/recent?limit=20");
  const now = useNow(5000);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Agents &amp; Pipeline</h1>
          <p className="muted">
            The 14 components of the loop, what each is doing now, and every model call they make.
          </p>
        </div>
      </header>

      <section className="panel">
        <h2>
          <Icon name="spark" /> Pipeline
        </h2>
        {overview.data ? <PipelineFlow stages={overview.data.pipeline} /> : <Skeleton h={120} />}
      </section>

      <section className="agent-grid" aria-label="Components">
        {(agents.data?.components ?? []).map((c) => (
          <article key={c.key} className={`agent-card st-${c.status}`}>
            <header>
              <span className="agent-icon">
                <Icon name={COMPONENT_ICON[c.key] ?? "cpu"} />
              </span>
              <h3>{c.name}</h3>
              <span className={`status-badge st-${c.status}`} title={STATUS_HELP[c.status]}>
                {c.status}
              </span>
            </header>
            <p className="small">{c.role}</p>
            <p className="muted small">{c.detail}</p>
            <footer className="muted small">
              {c.working_on ? (
                <Link href={`/incidents/${c.working_on}`} className="link">
                  working on INC-{String(c.working_on).padStart(5, "0")}
                </Link>
              ) : c.last_activity ? (
                `last activity ${ago(c.last_activity, now)} ago`
              ) : (
                "no activity yet"
              )}
            </footer>
          </article>
        ))}
        {!agents.data && <Skeleton h={200} />}
      </section>

      <div className="grid-2">
        <section className="panel">
          <h2>
            <Icon name="brain" /> Model calls
          </h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Agent</th>
                  <th>Model</th>
                  <th>Tokens in / out</th>
                  <th>Latency</th>
                  <th>Incident</th>
                </tr>
              </thead>
              <tbody>
                {(calls.data ?? []).map((c) => (
                  <tr key={c.id}>
                    <td>{ago(c.ts, now)} ago</td>
                    <td>{c.agent.replaceAll("_", " ")}</td>
                    <td className="mono">{c.model}</td>
                    <td>
                      {c.input_tokens.toLocaleString()} / {c.output_tokens.toLocaleString()}
                    </td>
                    <td>{c.latency_s.toFixed(1)}s</td>
                    <td>
                      {c.incident_id && (
                        <Link className="link" href={`/incidents/${c.incident_id}`}>
                          #{c.incident_id}
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {calls.data?.length === 0 && <p className="muted">No model calls yet.</p>}
          </div>
        </section>

        <section className="panel">
          <h2>
            <Icon name="eye" /> Recent watcher signals
          </h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Signal</th>
                  <th>Sev</th>
                  <th>Value</th>
                  <th>Fired</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {(signals.data ?? []).map((s) => (
                  <tr key={s.id}>
                    <td title={Object.entries(s.labels).map(([k, v]) => `${k}=${v}`).join(" ")}>
                      {s.incident_id ? (
                        <Link className="link" href={`/incidents/${s.incident_id}`}>
                          {s.signal_id}
                        </Link>
                      ) : (
                        s.signal_id
                      )}
                    </td>
                    <td>
                      <span className={`sev sev-${s.severity}`}>{s.severity}</span>
                    </td>
                    <td>{s.value >= 100 ? s.value.toFixed(0) : s.value.toPrecision(3)}</td>
                    <td>{ago(s.fired_at, now)} ago</td>
                    <td>{s.cleared_at ? <span className="muted">cleared</span> : <span className="bad-text">firing</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="panel">
        <h2>
          <Icon name="board" /> Job queue
        </h2>
        <div className="job-chips">
          {(agents.data?.jobs ?? []).map((j) => (
            <span key={`${j.queue}-${j.state}`} className={`job-chip js-${j.state}`}>
              <strong>{j.queue}</strong> {j.state} <span className="count">{j.count}</span>
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}
