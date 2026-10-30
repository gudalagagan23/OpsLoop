"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { ago, getJson, WING_LABEL, type Board as BoardData, type IncidentSummary } from "@/lib/api";
import { useLiveReload } from "./Live";

const SEVERITIES = ["S1", "S2", "S3", "S4"] as const;

export function Board() {
  const [data, setData] = useState<BoardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wing, setWing] = useState("all");
  const [sev, setSev] = useState<Set<string>>(new Set(SEVERITIES));
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    getJson<BoardData>("/board")
      .then((d) => {
        setData(d);
        setError(null);
        setNow(Date.now());
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useLiveReload(load);

  const wings = useMemo(() => {
    const all = new Set<string>();
    data?.columns.forEach((c) => c.incidents.forEach((i) => all.add(i.wing)));
    return [...all].sort();
  }, [data]);

  const visible = (i: IncidentSummary) => (wing === "all" || i.wing === wing) && sev.has(i.severity);
  const toggleSev = (s: string) =>
    setSev((cur) => {
      const next = new Set(cur);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  return (
    <section>
      <div className="toolbar">
        <h1>Issue board</h1>
        <div className="filters">
          <label>
            Wing{" "}
            <select value={wing} onChange={(e) => setWing(e.target.value)}>
              <option value="all">All wings</option>
              {wings.map((w) => (
                <option key={w} value={w}>
                  {WING_LABEL[w] ?? w}
                </option>
              ))}
            </select>
          </label>
          <span className="sev-filter" role="group" aria-label="Severity filter">
            {SEVERITIES.map((s) => (
              <button
                key={s}
                className={`sev sev-${s} ${sev.has(s) ? "" : "off"}`}
                aria-pressed={sev.has(s)}
                onClick={() => toggleSev(s)}
              >
                {s}
              </button>
            ))}
          </span>
        </div>
      </div>
      {error && <p className="error">Cannot reach the OpsLoop API: {error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <div className="board">
          {data.columns.map((col) => {
            const items = col.incidents.filter(visible);
            return (
              <div key={col.name} className="column">
                <header>
                  <span>{col.name}</span>
                  <span className="count">{items.length}</span>
                </header>
                <div className="cards">
                  {items.length === 0 && <p className="empty">Nothing here</p>}
                  {items.map((i) => (
                    <Link key={i.id} href={`/incidents/${i.id}`} className={`card sev-edge-${i.severity}`}>
                      <div className="card-top">
                        <span className={`sev sev-${i.severity}`}>{i.severity}</span>
                        <span className="tag">{WING_LABEL[i.wing] ?? i.wing}</span>
                        <span className="key">{i.key}</span>
                      </div>
                      <div className="card-title">{i.title}</div>
                      <div className="card-meta">
                        <span>{i.state.replaceAll("_", " ").toLowerCase()}</span>
                        <span>{ago(i.first_signal_at, now)} old</span>
                      </div>
                      {i.assignee && <div className="card-assignee">→ {i.assignee}</div>}
                    </Link>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
