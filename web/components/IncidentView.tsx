"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ago, getJson, WING_LABEL, type IncidentDetail } from "@/lib/api";
import { ACTOR_LABEL } from "@/lib/describe";
import { downloadText, incidentMarkdown } from "@/lib/report";
import { ApprovalDialog, DiffView, Tests } from "./ApprovalDialog";
import { Icon, WING_ICON } from "./Icon";
import { useLiveReload } from "./Live";
import { IncidentStepper } from "./Pipeline";
import { ReleasePanel } from "./ReleasePanel";

function fmt(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "–";
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3);
}

const WORKING: Record<string, string> = {
  OPEN: "Incident Manager is triaging",
  TRIAGED: "Queued for the Investigator",
  INVESTIGATING: "Investigator is reading metrics, logs and the knowledge base",
  REPORT1_READY: "Coding agent is choosing a strategy",
  FIXING: "A specialist is writing the fix in the sandbox",
  VALIDATING: "Test Loop is replaying the fault and running hidden tests",
  DEPLOYING: "Staged rollout in progress",
  VERIFYING: "Judge is comparing before and after",
};

export function IncidentView({ id }: { id: number }) {
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("approve") === "1") setReviewing(true);
  }, []);

  const load = useCallback(() => {
    getJson<IncidentDetail>(`/incidents/${id}`)
      .then((d) => {
        setInc(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [id]);
  useLiveReload(load);

  if (error && !inc) return <p className="banner bad">{error}</p>;
  if (!inc) return <div className="skeleton" style={{ height: 320 }} />;
  const r = inc.report1?.body;
  const r2 = inc.report2?.body;
  const cited = new Set([...(r?.evidence_for ?? []), ...(r?.evidence_against ?? [])]);
  const conf = r?.suspected_root_cause.confidence ?? 0;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");

  return (
    <article className="page detail">
      <Link href="/incidents" className="back">
        ← Incident board
      </Link>
      <header className="detail-head panel">
        <div className="detail-top">
          <div>
            <div className="card-top">
              <span className={`sev sev-${inc.severity}`}>{inc.severity}</span>
              <span className="tag">
                <Icon name={WING_ICON[inc.wing] ?? "info"} size={12} /> {WING_LABEL[inc.wing] ?? inc.wing}
              </span>
              <span className="key">{inc.key}</span>
            </div>
            <h1>{inc.title}</h1>
            <p className="muted">
              {inc.service} · first signal {ago(inc.first_signal_at)} ago · {inc.signal_count} signal
              {inc.signal_count === 1 ? "" : "s"}
              {inc.assignee ? ` · with ${inc.assignee}` : ""}
              {inc.closed_reason ? ` · closed: ${inc.closed_reason}` : ""}
            </p>
          </div>
          <div className="head-actions">
            <button
              className="btn primary"
              onClick={() => downloadText(`${inc.key}-report-${stamp}.md`, incidentMarkdown(inc))}
            >
              <Icon name="download" /> Download report
            </button>
            <button
              className="btn"
              onClick={() =>
                downloadText(`${inc.key}-evidence-${stamp}.json`, JSON.stringify(inc, null, 2), "application/json")
              }
            >
              <Icon name="ledger" /> Evidence JSON
            </button>
          </div>
        </div>
        <IncidentStepper state={inc.state} />
        {WORKING[inc.state] && (
          <p className="working">
            <span className="spinner" aria-hidden /> {WORKING[inc.state]}…
          </p>
        )}
      </header>

      {inc.state === "AWAITING_APPROVAL" && (
        <section className="banner approval-banner">
          <Icon name="hand" size={22} />
          <div>
            <strong>A validated fix is waiting for your decision</strong>
            <span className="muted">Approve, modify or reject this exact version.</span>
          </div>
          <button className="btn approve" onClick={() => setReviewing(true)}>
            Review &amp; decide
          </button>
        </section>
      )}

      <section className="panel">
        <h2>
          <Icon name="brain" /> Report 1 · Investigation{" "}
          {inc.report1?.is_stub && <span className="pill warn">Stage 1 stub</span>}
        </h2>
        {!r && <p className="muted">Not ready yet.</p>}
        {r && (
          <>
            <p className="lead">{r.summary}</p>
            <div className="rootcause">
              <div>
                <span className="muted small">Suspected root cause</span>
                <p>
                  <strong>{r.suspected_root_cause.description}</strong>
                </p>
                <span className="tag">{r.suspected_root_cause.category}</span>
              </div>
              <div className="confidence" aria-label={`Confidence ${Math.round(conf * 100)}%`}>
                <span className="muted small">Confidence</span>
                <div className="meter">
                  <span className={conf >= 0.6 ? "ok" : "warn"} style={{ width: `${conf * 100}%` }} />
                  <i style={{ left: "60%" }} title="Escalation threshold 0.6" />
                </div>
                <strong>{Math.round(conf * 100)}%</strong>
                <span className="muted small">{conf >= 0.6 ? "above" : "below"} the 60% escalation line</span>
              </div>
            </div>
            <dl className="facts">
              <dt>Evidence for</dt>
              <dd>
                {r.evidence_for.map((e) => (
                  <a key={e} href={`#ev-${e}`} className="evref">
                    #{e}
                  </a>
                ))}
              </dd>
              {r.evidence_against.length > 0 && (
                <>
                  <dt>Evidence against</dt>
                  <dd>
                    {r.evidence_against.map((e) => (
                      <a key={e} href={`#ev-${e}`} className="evref">
                        #{e}
                      </a>
                    ))}
                  </dd>
                </>
              )}
              <dt>Affected services</dt>
              <dd>{r.affected_services.join(", ")}</dd>
              <dt>Deploy correlation</dt>
              <dd>{r.deployment_correlation.relation}</dd>
              <dt>Uncertainty</dt>
              <dd>{r.uncertainty}</dd>
              <dt>Recommended</dt>
              <dd>
                {r.recommended_strategies.map((s) => (
                  <div key={s.strategy} className="strategy">
                    <strong>{s.strategy.replaceAll("_", " ")}</strong> <span className="tag">{s.risk} risk</span>{" "}
                    {s.rationale}
                  </div>
                ))}
              </dd>
            </dl>
          </>
        )}
      </section>

      {(r2 || inc.attempts.length > 0) && (
        <section className="panel">
          <h2>
            <Icon name="flask" /> Report 2 · Validation{" "}
            {r2 && <span className={`pill ${r2.verdict === "passed" ? "ok" : "bad"}`}>{r2.verdict}</span>}
          </h2>
          {inc.attempts.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Attempt</th>
                    <th>Strategy</th>
                    <th>Specialist</th>
                    <th>Status</th>
                    <th>Version</th>
                    <th>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {inc.attempts.map((a) => (
                    <tr key={a.attempt_no}>
                      <td>#{a.attempt_no}</td>
                      <td>{a.strategy.replaceAll("_", " ")}</td>
                      <td>{a.specialist.replaceAll("_", " ")}</td>
                      <td>
                        <span className={`pill ${a.status === "passed" ? "ok" : a.status === "failed" ? "bad" : "warn"}`}>
                          {a.status}
                        </span>
                      </td>
                      <td className="mono">{a.version_hash?.slice(0, 12) ?? "–"}</td>
                      <td>${a.cost_usd.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {r2 && (
            <>
              <h3 className="sub">Test Loop</h3>
              <div className="table-wrap">
                <Tests r={r2} />
              </div>
              {r2.performance && (
                <>
                  <h3 className="sub">Acceptance criteria</h3>
                  <div className="criteria">
                    {r2.performance.criteria.map((c) => (
                      <div key={c.metric} className={`criterion ${c.passed ? "ok" : "bad"}`}>
                        <span className="small">{c.label}</span>
                        <strong>
                          {fmt(r2.performance?.before[c.metric] ?? NaN)} → {fmt(c.actual)}
                        </strong>
                        <span className="muted small">
                          needs {c.op} {fmt(c.threshold)} · {c.passed ? "met" : "not met"}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
              <div className="panel-head">
                <h3 className="sub">Code change</h3>
                <button className="btn sm" onClick={() => setShowDiff(!showDiff)} aria-expanded={showDiff}>
                  {showDiff ? "Hide diff" : "Show diff"}
                </button>
              </div>
              {showDiff && <DiffView diff={r2.diff} />}
              <p className="muted small">
                Version <span className="mono">{r2.version_hash.slice(0, 16)}…</span> · rollback: {r2.rollback_plan}
              </p>
            </>
          )}
        </section>
      )}

      {inc.release && <ReleasePanel release={inc.release} state={inc.state} />}

      {inc.approval.decisions.length > 0 && (
        <section className="panel">
          <h2>
            <Icon name="shield" /> Decisions
          </h2>
          <ul className="decisions">
            {inc.approval.decisions.map((d) => (
              <li key={d.id}>
                <span className={`pill ${d.decision === "approve" ? "ok" : d.decision === "reject" ? "bad" : "warn"}`}>
                  {d.decision}
                </span>{" "}
                by <strong>{d.approver}</strong> · <span className="mono">{d.version_hash.slice(0, 12)}</span> ·{" "}
                {ago(d.created_at)} ago{d.reason ? ` · ${d.reason}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>
          <Icon name="activity" /> Signals
        </h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Signal</th>
                <th>Labels</th>
                <th>Sev</th>
                <th>Value</th>
                <th>Baseline</th>
                <th>Fired</th>
                <th>Cleared</th>
              </tr>
            </thead>
            <tbody>
              {inc.signals.map((s) => (
                <tr key={s.id}>
                  <td>{s.signal_id}</td>
                  <td className="mono">
                    {Object.entries(s.labels)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" ")}
                  </td>
                  <td>
                    <span className={`sev sev-${s.severity}`}>{s.severity}</span>
                  </td>
                  <td>{fmt(s.value)}</td>
                  <td>{fmt(s.baseline)}</td>
                  <td>{ago(s.fired_at)} ago</td>
                  <td>{s.cleared_at ? `${ago(s.cleared_at)} ago` : <span className="bad-text">firing</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid-2">
        <section className="panel">
          <h2>
            <Icon name="clock" /> Timeline
          </h2>
          <ol className="timeline">
            {inc.events.map((e) => (
              <li key={e.id}>
                <span className="muted small">{new Date(e.ts).toLocaleTimeString()}</span>{" "}
                {e.kind === "state_change" ? (
                  <>
                    <strong>{e.from_state?.replaceAll("_", " ")}</strong> → <strong>{e.to_state?.replaceAll("_", " ")}</strong>
                  </>
                ) : (
                  <strong>{e.kind.replaceAll("_", " ")}</strong>
                )}{" "}
                <span className="muted">by {ACTOR_LABEL[e.actor] ?? e.actor}</span>
                {e.reason && <div className="muted small">{e.reason}</div>}
              </li>
            ))}
          </ol>
        </section>

        <section className="panel">
          <h2>
            <Icon name="ledger" /> Evidence ledger
          </h2>
          <ul className="ledger chain">
            {inc.evidence.map((e) => (
              <li key={e.id} id={`ev-${e.id}`} className={cited.has(e.id) ? "cited" : ""}>
                <button className="ledger-row" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
                  <span className="mono">#{e.id}</span>
                  <span className="tag">{e.type}</span>
                  <span className="muted">{e.source}</span>
                  {cited.has(e.id) && <span className="pill ok">cited</span>}
                  <span className="mono hash" title={e.hash}>
                    {e.hash.slice(0, 10)}…
                  </span>
                </button>
                {open === e.id && (
                  <pre className="payload">{JSON.stringify({ ...e.payload, prev_hash: e.prev_hash, hash: e.hash }, null, 2)}</pre>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
      {reviewing && (
        <ApprovalDialog
          incident={inc}
          onClose={() => setReviewing(false)}
          onDone={() => {
            setReviewing(false);
            load();
          }}
        />
      )}
    </article>
  );
}
