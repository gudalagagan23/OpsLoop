"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ago, getJson, WING_LABEL, type IncidentDetail } from "@/lib/api";
import { ApprovalDialog } from "./ApprovalDialog";
import { useLiveReload } from "./Live";

function fmt(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "–";
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3);
}

export function IncidentView({ id }: { id: number }) {
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [reviewing, setReviewing] = useState(false);

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

  if (error) return <p className="error">{error}</p>;
  if (!inc) return <p className="muted">Loading…</p>;
  const r = inc.report1?.body;
  const cited = new Set([...(r?.evidence_for ?? []), ...(r?.evidence_against ?? [])]);

  return (
    <article className="detail">
      <Link href="/" className="back">
        ← Board
      </Link>
      <header className="detail-head">
        <div className="card-top">
          <span className={`sev sev-${inc.severity}`}>{inc.severity}</span>
          <span className="tag">{WING_LABEL[inc.wing] ?? inc.wing}</span>
          <span className="key">{inc.key}</span>
          <span className="state">{inc.state.replaceAll("_", " ")}</span>
        </div>
        <h1>{inc.title}</h1>
        <p className="muted">
          {inc.service} · first signal {ago(inc.first_signal_at)} ago · {inc.signal_count} signal
          {inc.signal_count === 1 ? "" : "s"}
          {inc.closed_reason ? ` · closed: ${inc.closed_reason}` : ""}
        </p>
      </header>

      <section className="panel">
        <h2>
          Report 1 · Investigation {inc.report1?.is_stub && <span className="pill warn">Stage 1 stub</span>}
        </h2>
        {!r && <p className="muted">Not ready yet.</p>}
        {r && (
          <>
            <p>{r.summary}</p>
            <dl className="facts">
              <dt>Suspected root cause</dt>
              <dd>
                {r.suspected_root_cause.description} ({r.suspected_root_cause.category}, confidence{" "}
                {r.suspected_root_cause.confidence.toFixed(2)})
              </dd>
              <dt>Evidence for</dt>
              <dd>
                {r.evidence_for.map((e) => (
                  <a key={e} href={`#ev-${e}`} className="evref">
                    #{e}
                  </a>
                ))}
              </dd>
              <dt>Affected services</dt>
              <dd>{r.affected_services.join(", ")}</dd>
              <dt>Deploy correlation</dt>
              <dd>{r.deployment_correlation.relation}</dd>
              <dt>Uncertainty</dt>
              <dd>{r.uncertainty}</dd>
              <dt>Recommended</dt>
              <dd>
                {r.recommended_strategies.map((s) => (
                  <div key={s.strategy}>
                    <strong>{s.strategy}</strong> ({s.risk} risk): {s.rationale}
                  </div>
                ))}
              </dd>
            </dl>
          </>
        )}
      </section>

      {inc.state === "AWAITING_APPROVAL" && (
        <section className="panel callout">
          <h2>A validated fix is waiting for your decision</h2>
          <button className="btn approve" onClick={() => setReviewing(true)}>
            Review &amp; decide
          </button>
        </section>
      )}

      {inc.report2 && (
        <section className="panel">
          <h2>
            Report 2 · Validation{" "}
            <span className={`pill ${inc.report2.body.verdict === "passed" ? "ok" : "bad"}`}>
              {inc.report2.body.verdict}
            </span>
          </h2>
          <table>
            <thead>
              <tr>
                <th>Attempt</th>
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
                  <td>{a.specialist}</td>
                  <td>{a.status}</td>
                  <td className="mono">{a.version_hash?.slice(0, 12) ?? "–"}</td>
                  <td>${a.cost_usd.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="panel">
        <h2>Signals</h2>
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
                  <td>{s.cleared_at ? `${ago(s.cleared_at)} ago` : "firing"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="two-col">
        <section className="panel">
          <h2>Timeline</h2>
          <ol className="timeline">
            {inc.events.map((e) => (
              <li key={e.id}>
                <span className="muted">{new Date(e.ts).toLocaleTimeString()}</span>{" "}
                {e.kind === "state_change" ? (
                  <>
                    <strong>{e.from_state}</strong> → <strong>{e.to_state}</strong>
                  </>
                ) : (
                  <strong>{e.kind.replaceAll("_", " ")}</strong>
                )}{" "}
                <span className="muted">by {e.actor}</span>
                {e.reason && <div className="muted">{e.reason}</div>}
              </li>
            ))}
          </ol>
        </section>

        <section className="panel">
          <h2>Evidence ledger</h2>
          <ul className="ledger">
            {inc.evidence.map((e) => (
              <li key={e.id} id={`ev-${e.id}`} className={cited.has(e.id) ? "cited" : ""}>
                <button className="ledger-row" onClick={() => setOpen(open === e.id ? null : e.id)}>
                  <span className="mono">#{e.id}</span>
                  <span className="tag">{e.type}</span>
                  <span className="muted">{e.source}</span>
                  <span className="mono hash" title={e.hash}>
                    {e.hash.slice(0, 10)}…
                  </span>
                </button>
                {open === e.id && (
                  <pre className="payload">
                    {JSON.stringify({ ...e.payload, prev_hash: e.prev_hash, hash: e.hash }, null, 2)}
                  </pre>
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
