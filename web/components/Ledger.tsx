"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ago, type LedgerEntry } from "@/lib/api";
import { Skeleton } from "./CommandCenter";
import { Icon } from "./Icon";
import { useApi, useNow } from "./useApi";

type Verify = { ok: boolean; checked: number; first_bad_id: number | null; reason?: string | null };

export function Ledger() {
  const entries = useApi<LedgerEntry[]>("/ledger/recent?limit=150");
  const verify = useApi<Verify>("/ledger/verify");
  const [type, setType] = useState("all");
  const [open, setOpen] = useState<number | null>(null);
  const now = useNow(5000);
  const types = useMemo(() => [...new Set((entries.data ?? []).map((e) => e.type))].sort(), [entries.data]);
  const shown = (entries.data ?? []).filter((e) => type === "all" || e.type === type);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Evidence Ledger</h1>
          <p className="muted">
            Every signal, log excerpt, retrieval, model output, test run and decision is appended here.
            Each entry stores the hash of the one before it, and the database refuses edits and deletes.
          </p>
        </div>
        <button className="btn primary" onClick={verify.reload}>
          <Icon name="refresh" /> Verify chain now
        </button>
      </header>

      <section className={`banner ${verify.data ? (verify.data.ok ? "good" : "bad") : ""}`}>
        <Icon name={verify.data?.ok ? "check" : "alert"} size={22} />
        <div>
          {verify.data ? (
            verify.data.ok ? (
              <strong>Chain intact: all {verify.data.checked.toLocaleString()} entries verified.</strong>
            ) : (
              <strong>
                Chain broken at entry #{verify.data.first_bad_id}
                {verify.data.reason ? `: ${verify.data.reason}` : ""}
              </strong>
            )
          ) : (
            <strong>Verifying…</strong>
          )}
          <span className="muted">Recomputes sha256(prev_hash + entry) for every row.</span>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>
            <Icon name="ledger" /> Recent entries
          </h2>
          <label className="small">
            Type{" "}
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="all">All types</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!entries.data && <Skeleton h={200} />}
        <ul className="ledger chain">
          {shown.map((e) => (
            <li key={e.id}>
              <button className="ledger-row" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
                <span className="mono">#{e.id}</span>
                <span className="tag">{e.type}</span>
                <span className="muted">{e.source}</span>
                {e.incident_id && <span className="muted small">INC-{String(e.incident_id).padStart(5, "0")}</span>}
                <span className="muted small hide-sm">{ago(e.ts, now)} ago</span>
                <span className="mono hash" title={e.hash}>
                  {e.prev_hash.slice(0, 6)} → {e.hash.slice(0, 10)}
                </span>
              </button>
              {open === e.id && (
                <div className="payload-wrap">
                  {e.incident_id && (
                    <Link className="link small" href={`/incidents/${e.incident_id}`}>
                      Open incident →
                    </Link>
                  )}
                  <pre className="payload">{JSON.stringify({ ...e.payload, prev_hash: e.prev_hash, hash: e.hash }, null, 2)}</pre>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
