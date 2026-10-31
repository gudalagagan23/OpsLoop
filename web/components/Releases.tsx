"use client";

import Link from "next/link";
import { ago, type Releases as ReleasesData } from "@/lib/api";
import { Skeleton } from "./CommandCenter";
import { Icon } from "./Icon";
import { useApi, useNow } from "./useApi";

const STAGES = [5, 25, 100];
const VERDICT_CLASS: Record<string, string> = {
  RESOLVED: "ok",
  PARTIAL: "warn",
  UNSUCCESSFUL: "bad",
  INCONCLUSIVE: "warn",
};

export function Releases() {
  const { data, error } = useApi<ReleasesData>("/releases", 10000);
  const now = useNow(5000);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Releases &amp; Judge</h1>
          <p className="muted">
            An approved fix rolls out to 5%, then 25%, then 100% of traffic. Health rules watch each
            step and roll back on their own. The Judge then compares before and after.
          </p>
        </div>
      </header>
      {error && !data && <p className="banner bad">Cannot reach the OpsLoop API: {error}</p>}
      {!data && !error && <Skeleton h={160} />}

      <section className="panel">
        <h2>
          <Icon name="rocket" /> Staged rollouts
        </h2>
        <ol className="rollout-legend" aria-hidden>
          {STAGES.map((s) => (
            <li key={s}>{s}%</li>
          ))}
        </ol>
        {data?.deployments.length === 0 && (
          <div className="empty-state">
            <Icon name="shield" size={28} />
            <p>
              <strong>No releases yet.</strong>
              <br />
              <span className="muted">A rollout starts only after a human approves a validated fix.</span>
            </p>
          </div>
        )}
        <ul className="rollouts">
          {data?.deployments.map((d) => (
            <li key={d.id} className={`rollout st-${d.state}`}>
              <div className="rollout-head">
                <Link href={`/incidents/${d.incident_id}`} className="link">
                  INC-{String(d.incident_id).padStart(5, "0")}
                </Link>
                <span className="tag">{d.kind}</span>
                <span className="mono">{d.version_hash.slice(0, 12)}</span>
                <span className={`pill ${d.state === "completed" ? "ok" : d.state === "rolled_back" ? "bad" : "warn"}`}>
                  {d.state.replaceAll("_", " ")}
                </span>
                <span className="muted small">started {ago(d.started_at, now)} ago</span>
              </div>
              <div className="rollout-bar" role="progressbar" aria-valuenow={d.stage_pct} aria-valuemin={0} aria-valuemax={100}>
                <span style={{ width: `${d.stage_pct}%` }} />
                {STAGES.map((s) => (
                  <i key={s} style={{ left: `${s}%` }} className={d.stage_pct >= s ? "reached" : ""} />
                ))}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>
          <Icon name="scale" /> Judge verdicts
        </h2>
        {data?.outcomes.length === 0 && <p className="muted">No verdicts yet. They follow each completed release.</p>}
        <ul className="verdicts">
          {data?.outcomes.map((o) => (
            <li key={o.id}>
              <span className={`pill ${VERDICT_CLASS[o.verdict] ?? ""}`}>{o.verdict}</span>
              <Link href={`/incidents/${o.incident_id}`} className="link">
                INC-{String(o.incident_id).padStart(5, "0")}
              </Link>
              <span className="tag">{o.mode}</span>
              <span className="muted small">
                {o.summary?.rollout === "rolled_back" ? `rolled back at ${o.summary.stage_reached ?? 0}%` : `${o.summary?.candidate_samples ?? 0} requests measured on the fix`}
              </span>
              <span className="muted small">{ago(o.created_at, now)} ago</span>
              {o.explanation && <p className="muted small verdict-why">{o.explanation}</p>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
