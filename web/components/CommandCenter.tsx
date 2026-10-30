"use client";

import Link from "next/link";
import { useState } from "react";
import {
  ago,
  duration,
  WING_LABEL,
  type ActivityRow,
  type AgentsInfo,
  type ApprovalsQueue,
  type Department,
  type LiveMetrics,
  type Overview,
} from "@/lib/api";
import { ACTOR_LABEL, describe, toneOf } from "@/lib/describe";
import { LineChart, SeverityBar } from "./Charts";
import { Icon, WING_ICON } from "./Icon";
import { PipelineFlow } from "./Pipeline";
import { useApi, useNow } from "./useApi";

const pct = (v: number) => `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`;
const num = (v: number) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));

export const METRICS: {
  key: string;
  title: string;
  format: (v: number) => string;
  threshold?: number;
  thresholdLabel?: string;
  hint: string;
}[] = [
  { key: "rps", title: "Requests / second", format: num, hint: "Simulated tenant traffic hitting TaskFlow" },
  {
    key: "error_rate",
    title: "Error rate (5xx)",
    format: pct,
    threshold: 0.02,
    thresholdLabel: "warn 2%",
    hint: "API watcher raises S1 at 5%",
  },
  {
    key: "p95_ms",
    title: "p95 latency",
    format: (v) => `${v.toFixed(0)} ms`,
    threshold: 300,
    thresholdLabel: "SLO 300 ms",
    hint: "All routes except health and metrics",
  },
  {
    key: "queries_per_request",
    title: "DB queries per request",
    format: num,
    threshold: 10,
    thresholdLabel: "warn 10",
    hint: "p95 on the task list route (N+1 shows up here)",
  },
  { key: "pool_in_use", title: "DB connections in use", format: (v) => v.toFixed(0), hint: "Pool leak shows up as a climb that never falls" },
];

export function CommandCenter() {
  const [minutes, setMinutes] = useState(30);
  const overview = useApi<Overview>("/overview", 10000);
  const metrics = useApi<LiveMetrics>(`/metrics/live?minutes=${minutes}`, 15000, 1500);
  const departments = useApi<Department[]>("/departments", 15000);
  const activity = useApi<ActivityRow[]>("/activity?limit=14");
  const approvals = useApi<ApprovalsQueue>("/approvals", 15000);
  const agents = useApi<AgentsInfo>("/agents", 15000);
  const now = useNow(5000);
  const o = overview.data;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Command Center</h1>
          <p className="muted">
            Live view of the TaskFlow estate and every OpsLoop agent. Updates as events stream in.
          </p>
        </div>
        <div className="head-actions">
          <Link href="/simulation" className="btn">
            <Icon name="flask" /> Inject a fault
          </Link>
          <Link href="/incidents" className="btn primary">
            <Icon name="board" /> Open incident board
          </Link>
        </div>
      </header>

      {overview.error && !o && <p className="banner bad">Cannot reach the OpsLoop API: {overview.error}</p>}

      {approvals.data && approvals.data.pending.length > 0 && (
        <section className="banner approval-banner" aria-live="polite">
          <Icon name="hand" size={22} />
          <div>
            <strong>
              {approvals.data.pending.length} validated fix{approvals.data.pending.length > 1 ? "es" : ""} waiting
              for your approval
            </strong>
            <span className="muted">
              {approvals.data.pending[0].key} · {approvals.data.pending[0].title}
            </span>
          </div>
          <Link href={`/incidents/${approvals.data.pending[0].id}?approve=1`} className="btn approve">
            Review &amp; approve
          </Link>
        </section>
      )}

      <section className="kpis" aria-label="Key figures">
        <Kpi icon="alert" label="Active incidents" value={o?.incidents.active} tone={o?.incidents.active ? "bad" : "good"}>
          {o && <SeverityBar counts={o.incidents.by_severity} />}
        </Kpi>
        <Kpi
          icon="shield"
          label="Awaiting approval"
          value={o?.approvals_pending}
          tone={o?.approvals_pending ? "warn" : undefined}
          foot={<Link href="/approvals">Approval queue →</Link>}
        />
        <Kpi
          icon="activity"
          label="Opened / cleared (24h)"
          value={o ? `${o.incidents.opened_24h} / ${Object.values(o.incidents.closed_24h).reduce((a, b) => a + b, 0)}` : undefined}
          foot={o && o.escalated ? <span className="bad-text">{o.escalated} escalated to a human</span> : "none escalated"}
        />
        <Kpi
          icon="clock"
          label="Median signal → Report 1"
          value={o ? duration(o.timings.median_signal_to_report1_s) : undefined}
          foot={`to approval: ${o ? duration(o.timings.median_signal_to_approval_s) : "–"}`}
        />
        <Kpi
          icon="flask"
          label="Fix validation pass rate"
          value={o ? (o.fixes.pass_rate === null ? "–" : pct(o.fixes.pass_rate)) : undefined}
          foot={o ? `${o.fixes.validated} of ${o.fixes.attempts} attempts passed` : ""}
        />
        <Kpi
          icon="brain"
          label="LLM calls (24h)"
          value={o?.llm.calls_24h}
          foot={o ? `${(o.llm.tokens_24h / 1000).toFixed(1)}k tokens · ${o.llm.model ?? "no model"} · $${o.llm.cost_usd_24h.toFixed(2)}` : ""}
        />
        <Kpi
          icon="ledger"
          label="Evidence ledger"
          value={o?.ledger_entries}
          foot={<Link href="/ledger">hash-chained, append-only →</Link>}
        />
        <Kpi icon="book" label="Knowledge chunks" value={o?.knowledge_chunks} foot="runbooks, past incidents, fixes" />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>
            <Icon name="spark" /> The loop, live
          </h2>
          <span className="muted small">Incidents currently at each stage · click a stage to open it</span>
        </div>
        {o ? <PipelineFlow stages={o.pipeline} /> : <Skeleton h={120} />}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>
            <Icon name="activity" /> System behaviour
          </h2>
          <div className="seg" role="group" aria-label="Time range">
            {[15, 30, 60, 180].map((m) => (
              <button key={m} className={m === minutes ? "on" : ""} aria-pressed={m === minutes} onClick={() => setMinutes(m)}>
                {m < 60 ? `${m}m` : `${m / 60}h`}
              </button>
            ))}
          </div>
        </div>
        {metrics.data && !metrics.data.available && (
          <p className="muted">Prometheus is not reachable from the API right now; charts resume when it is.</p>
        )}
        <div className="charts">
          {METRICS.map((m) => (
            <LineChart
              key={m.key}
              title={m.title}
              hint={m.hint}
              points={metrics.data?.series[m.key] ?? []}
              format={m.format}
              threshold={m.threshold}
              thresholdLabel={m.thresholdLabel}
            />
          ))}
        </div>
      </section>

      <div className="grid-2">
        <section className="panel">
          <div className="panel-head">
            <h2>
              <Icon name="building" /> Departments
            </h2>
            <Link href="/departments" className="small link">
              All six wings →
            </Link>
          </div>
          <div className="dept-mini">
            {(departments.data ?? []).map((d) => (
              <Link key={d.wing} href={`/departments#${d.wing}`} className={`dept-chip st-${d.status}`}>
                <Icon name={WING_ICON[d.wing] ?? "info"} />
                <span className="dept-chip-name">{d.name}</span>
                <span className="dept-chip-status">{statusText(d)}</span>
              </Link>
            ))}
            {!departments.data && <Skeleton h={140} />}
          </div>
          <h3 className="sub">Agents</h3>
          <ul className="agent-mini">
            {(agents.data?.components ?? []).map((c) => (
              <li key={c.key} className={`st-${c.status}`} title={`${c.role} · ${c.detail}`}>
                <span className="status-dot" aria-hidden />
                {c.name}
                <span className="muted small">{c.status}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>
              <Icon name="bell" /> Live activity
            </h2>
            <span className="pill ok live">streaming</span>
          </div>
          <ol className="feed">
            {(activity.data ?? []).map((a) => (
              <li key={a.event_id} className={`feed-item tone-${toneOf(a)}`}>
                <span className="feed-dot" aria-hidden />
                <div>
                  <Link href={`/incidents/${a.incident.id}`}>
                    <strong>{describe(a)}</strong>
                    <span className="muted"> · {a.incident.key}</span>
                  </Link>
                  <div className="muted small">
                    {WING_LABEL[a.incident.wing] ?? a.incident.wing} · {ACTOR_LABEL[a.actor] ?? a.actor} ·{" "}
                    {ago(a.ts, now)} ago
                  </div>
                  {a.reason && <div className="small feed-reason">{a.reason}</div>}
                </div>
              </li>
            ))}
            {activity.data?.length === 0 && <li className="muted">Quiet so far.</li>}
          </ol>
        </section>
      </div>
    </div>
  );
}

export function statusText(d: Department): string {
  if (d.status === "incident") return `${d.active_incidents.length} active`;
  if (d.status === "healthy") return "healthy";
  if (d.status === "stale") return "watcher silent";
  return `planned · ${d.planned_stage ?? "later"}`;
}

function Kpi({
  icon,
  label,
  value,
  tone,
  foot,
  children,
}: {
  icon: string;
  label: string;
  value: number | string | undefined;
  tone?: "good" | "bad" | "warn";
  foot?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <div className="kpi-label">
        <Icon name={icon} size={16} />
        {label}
      </div>
      <div className="kpi-value">{value ?? <span className="skeleton-inline" />}</div>
      {children}
      {foot && <div className="kpi-foot">{foot}</div>}
    </div>
  );
}

export function Skeleton({ h }: { h: number }) {
  return <div className="skeleton" style={{ height: h }} aria-hidden />;
}
