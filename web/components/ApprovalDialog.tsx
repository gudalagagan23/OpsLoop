"use client";

import { useEffect, useRef, useState } from "react";
import {
  getToken,
  postJson,
  setToken,
  type IncidentDetail,
  type Report2,
} from "@/lib/api";

type Mode = "review" | "modify" | "reject";

export function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff" aria-label="Proposed code change">
      {diff.split("\n").map((line, i) => {
        const cls = line.startsWith("+") && !line.startsWith("+++")
          ? "add"
          : line.startsWith("-") && !line.startsWith("---")
            ? "del"
            : line.startsWith("@@")
              ? "hunk"
              : "";
        return (
          <span key={i} className={cls}>
            {line + "\n"}
          </span>
        );
      })}
    </pre>
  );
}

function fmt(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : String(Math.round(v * 100) / 100);
}

export function Tests({ r }: { r: Report2 }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Step</th>
          <th>Result</th>
          <th>Time</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {r.tests.map((t) => (
          <tr key={t.step}>
            <td>{t.step.replaceAll("_", " ")}</td>
            <td>
              <span className={`pill ${t.status === "passed" ? "ok" : "bad"}`}>{t.status}</span>
            </td>
            <td>{t.seconds}s</td>
            <td className="muted">{t.summary}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ApprovalDialog({
  incident,
  onClose,
  onDone,
}: {
  incident: IncidentDetail;
  onClose: () => void;
  onDone: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const r2 = incident.report2?.body ?? null;
  const r1 = incident.report1?.body ?? null;
  const hash = incident.approval.pending_version_hash ?? "";
  const [mode, setMode] = useState<Mode>("review");
  const [token, setTok] = useState("");
  const [patch, setPatch] = useState(r2?.diff ?? "");
  const [reason, setReason] = useState("");
  const [followUp, setFollowUp] = useState("retry");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTok(getToken());
    const d = ref.current;
    // No close() on cleanup: it fires onClose, and React's effect re-run would then dismiss
    // the dialog it just opened. Unmounting removes the element anyway.
    if (d && !d.open) d.showModal();
  }, []);

  const submit = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      setToken(token);
      await postJson(`/incidents/${incident.id}/approval`, { version_hash: hash, ...body }, token);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const latestApproval = incident.approval.decisions.at(-1);

  return (
    <dialog ref={ref} className="approval" onClose={onClose} aria-labelledby="approval-title">
      <header className="approval-head">
        <div>
          <h2 id="approval-title">
            Review fix · {incident.key}{" "}
            <span className={`sev sev-${incident.severity}`}>{incident.severity}</span>
          </h2>
          <p className="muted">
            {incident.title} · strategy <strong>{r2?.strategy ?? "?"}</strong>
          </p>
        </div>
        <button className="toast-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </header>

      {!r2 || !hash ? (
        <p className="muted">No validated change is waiting for a decision.</p>
      ) : (
        <div className="approval-body">
          {r1 && (
            <section>
              <h3>Why (Report 1)</h3>
              <p>{r1.suspected_root_cause.description}</p>
              <p className="muted">
                {r1.suspected_root_cause.category} · confidence{" "}
                {r1.suspected_root_cause.confidence.toFixed(2)} · deploy correlation{" "}
                {r1.deployment_correlation.relation}
              </p>
            </section>
          )}

          <section>
            <h3>Code change</h3>
            {mode === "modify" ? (
              <textarea
                className="patch-edit"
                value={patch}
                onChange={(e) => setPatch(e.target.value)}
                spellCheck={false}
                aria-label="Edit the patch"
              />
            ) : (
              <DiffView diff={r2.diff} />
            )}
          </section>

          <section>
            <h3>Validation (Report 2)</h3>
            <Tests r={r2} />
            {r2.performance && (
              <table>
                <thead>
                  <tr>
                    <th>Acceptance criterion</th>
                    <th>Before</th>
                    <th>After</th>
                    <th>Required</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {r2.performance.criteria.map((c) => (
                    <tr key={c.metric}>
                      <td>{c.label}</td>
                      <td>{fmt(r2.performance?.before[c.metric] ?? NaN)}</td>
                      <td>{fmt(c.actual)}</td>
                      <td>
                        {c.op} {fmt(c.threshold)}
                      </td>
                      <td>
                        <span className={`pill ${c.passed ? "ok" : "bad"}`}>
                          {c.passed ? "met" : "not met"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="two-col">
            <div>
              <h3>Security findings</h3>
              {r2.security_findings.length === 0 ? (
                <p className="muted">None: policy and static scan are clean.</p>
              ) : (
                <ul>
                  {r2.security_findings.map((f, i) => (
                    <li key={i}>
                      {f.rule}: {f.detail}
                    </li>
                  ))}
                </ul>
              )}
              <h3>Remaining risks</h3>
              <ul>
                {r2.remaining_risks.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Rollback plan</h3>
              <p>{r2.rollback_plan}</p>
              <h3>Version</h3>
              <p className="mono hash-full">{hash}</p>
              <p className="muted">
                Your decision applies only to this exact version. If it changes, you will be asked
                to review again.
              </p>
            </div>
          </section>

          {latestApproval && (
            <p className="muted">
              Last decision: {latestApproval.decision} by {latestApproval.approver}
            </p>
          )}

          <section className="decide">
            <label>
              Approver token{" "}
              <input
                type="password"
                value={token}
                onChange={(e) => setTok(e.target.value)}
                placeholder="paste your OPSLOOP approver token"
                autoComplete="off"
              />
            </label>
            {mode === "reject" && (
              <div className="reject-form">
                <label>
                  Reason (required)
                  <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
                </label>
                <label>
                  Then{" "}
                  <select value={followUp} onChange={(e) => setFollowUp(e.target.value)}>
                    <option value="retry">retry with my note</option>
                    <option value="change_strategy">change strategy</option>
                    <option value="take_over">I will fix it myself</option>
                  </select>
                </label>
              </div>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div className="buttons">
              {mode === "review" && (
                <>
                  <button
                    className="btn approve"
                    disabled={busy || !token}
                    onClick={() => submit({ decision: "approve" })}
                  >
                    Approve this version
                  </button>
                  <button className="btn modify" onClick={() => setMode("modify")}>
                    Modify
                  </button>
                  <button className="btn reject" onClick={() => setMode("reject")}>
                    Reject
                  </button>
                </>
              )}
              {mode === "modify" && (
                <>
                  <button
                    className="btn modify"
                    disabled={busy || !token || !patch.trim()}
                    onClick={() => submit({ decision: "modify", patch })}
                  >
                    Submit edit for re-validation
                  </button>
                  <button className="btn" onClick={() => setMode("review")}>
                    Back
                  </button>
                </>
              )}
              {mode === "reject" && (
                <>
                  <button
                    className="btn reject"
                    disabled={busy || !token || !reason.trim()}
                    onClick={() => submit({ decision: "reject", reason, follow_up: followUp })}
                  >
                    Confirm rejection
                  </button>
                  <button className="btn" onClick={() => setMode("review")}>
                    Back
                  </button>
                </>
              )}
            </div>
          </section>
        </div>
      )}
    </dialog>
  );
}
