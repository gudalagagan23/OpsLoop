"use client";

import Link from "next/link";
import { ago, WING_LABEL, type ApprovalsQueue } from "@/lib/api";
import { useAuth } from "./Auth";
import { Skeleton } from "./CommandCenter";
import { Icon } from "./Icon";
import { useApi, useNow } from "./useApi";

const DECISION_CLASS: Record<string, string> = { approve: "ok", modify: "warn", reject: "bad" };

export function Approvals() {
  const { data, error } = useApi<ApprovalsQueue>("/approvals", 10000);
  const { me } = useAuth();
  const now = useNow(5000);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Approvals</h1>
          <p className="muted">
            Nothing reaches production without a person approving the exact version that passed
            validation. Approval is bound to its version hash: any change asks again.
          </p>
        </div>
        {!me && <span className="pill warn">Sign in with an approver token to decide</span>}
        {me && (
          <span className={`pill ${me.role === "approver" ? "ok" : "warn"}`}>
            {me.name} · {me.role}
          </span>
        )}
      </header>
      {error && !data && <p className="banner bad">Cannot reach the OpsLoop API: {error}</p>}
      {!data && !error && <Skeleton h={160} />}

      {data && (
        <>
          <section className="panel">
            <h2>
              <Icon name="hand" /> Waiting for a decision <span className="count">{data.pending.length}</span>
            </h2>
            {data.pending.length === 0 && (
              <div className="empty-state">
                <Icon name="check" size={28} />
                <p>
                  <strong>Queue is clear.</strong>
                  <br />
                  <span className="muted">
                    When the Test Loop validates a fix it lands here and a pop-up appears in the corner.
                  </span>
                </p>
              </div>
            )}
            <div className="approval-cards">
              {data.pending.map((p) => (
                <article key={p.id} className={`approval-card sev-edge-${p.severity}`}>
                  <div className="card-top">
                    <span className={`sev sev-${p.severity}`}>{p.severity}</span>
                    <span className="tag">{WING_LABEL[p.wing] ?? p.wing}</span>
                    <span className="key">{p.key}</span>
                  </div>
                  <h3>{p.title}</h3>
                  <dl className="dept-facts">
                    <dt>Fixed by</dt>
                    <dd>{p.specialist?.replaceAll("_", " ") ?? "–"}</dd>
                    <dt>Version</dt>
                    <dd className="mono">{p.version_hash?.slice(0, 16) ?? "–"}…</dd>
                    <dt>Waiting</dt>
                    <dd>{p.waiting_since ? `${ago(p.waiting_since, now)}` : "–"}</dd>
                  </dl>
                  <div className="buttons">
                    <Link href={`/incidents/${p.id}?approve=1`} className="btn approve">
                      Review &amp; decide
                    </Link>
                    <Link href={`/incidents/${p.id}`} className="btn">
                      Full incident
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>
              <Icon name="scale" /> Decision rates
            </h2>
            <div className="rate-row">
              {["approve", "modify", "reject"].map((d) => (
                <div key={d} className={`rate rate-${DECISION_CLASS[d]}`}>
                  <span className="rate-label">{d}</span>
                  <span className="rate-value">{data.rates.counts[d] ?? 0}</span>
                  <span className="muted small">
                    {data.rates.total ? `${Math.round((data.rates.rates[d] ?? 0) * 100)}% of decisions` : "no decisions yet"}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>
              <Icon name="clock" /> History
            </h2>
            {data.history.length === 0 ? (
              <p className="muted">No decisions recorded yet.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Incident</th>
                      <th>Decision</th>
                      <th>By</th>
                      <th>Version</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.history.map((h) => (
                      <tr key={h.id}>
                        <td>{ago(h.created_at, now)} ago</td>
                        <td>
                          <Link className="link" href={`/incidents/${h.incident.id}`}>
                            {h.incident.key}
                          </Link>
                        </td>
                        <td>
                          <span className={`pill ${DECISION_CLASS[h.decision] ?? ""}`}>{h.decision}</span>
                        </td>
                        <td>{h.approver}</td>
                        <td className="mono">{h.version_hash.slice(0, 12)}</td>
                        <td className="wrap">{h.reason ?? "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
