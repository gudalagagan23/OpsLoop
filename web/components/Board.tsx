"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ago, getJson, WING_LABEL, type Board as BoardData, type IncidentSummary } from "@/lib/api";
import { Icon, WING_ICON } from "./Icon";
import { useLiveReload } from "./Live";
import { useNow } from "./useApi";

const SEVERITIES = ["S1", "S2", "S3", "S4"] as const;
const ALL_WINGS = ["api", "database", "auth", "billing", "infra", "ux", "meta"];

export function Board() {
  const [data, setData] = useState<BoardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wing, setWing] = useState("all");
  const [query, setQuery] = useState("");
  const [sev, setSev] = useState<Set<string>>(new Set(SEVERITIES));
  const [showClosed, setShowClosed] = useState(true);
  const now = useNow(5000);

  useEffect(() => {
    const w = new URLSearchParams(window.location.search).get("wing");
    if (w) setWing(w);
  }, []);

  const pickWing = (w: string) => {
    setWing(w);
    const url = new URL(window.location.href);
    if (w === "all") url.searchParams.delete("wing");
    else url.searchParams.set("wing", w);
    window.history.replaceState(null, "", url);
  };

  const load = useCallback(() => {
    getJson<BoardData>("/board")
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useLiveReload(load);

  const q = query.trim().toLowerCase();
  const visible = (i: IncidentSummary) =>
    (wing === "all" || i.wing === wing) &&
    sev.has(i.severity) &&
    (!q || `${i.key} ${i.title} ${i.service} ${i.state}`.toLowerCase().includes(q));
  const toggleSev = (s: string) =>
    setSev((cur) => {
      const next = new Set(cur);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  const columns = useMemo(
    () => (data?.columns ?? []).filter((c) => showClosed || c.name !== "Closed"),
    [data, showClosed],
  );
  const total = columns.reduce((n, c) => n + c.incidents.filter(visible).length, 0);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Incident Board</h1>
          <p className="muted">
            Every incident from every department, moving through the loop on its own. {total} shown.
          </p>
        </div>
      </header>
      <div className="toolbar">
        <div className="wing-tabs" role="tablist" aria-label="Department">
          <button role="tab" aria-selected={wing === "all"} className={wing === "all" ? "on" : ""} onClick={() => pickWing("all")}>
            All departments
          </button>
          {ALL_WINGS.map((w) => (
            <button key={w} role="tab" aria-selected={wing === w} className={wing === w ? "on" : ""} onClick={() => pickWing(w)}>
              <Icon name={WING_ICON[w] ?? "info"} size={14} /> {WING_LABEL[w] ?? w}
            </button>
          ))}
        </div>
        <div className="filters">
          <input
            type="search"
            className="search"
            placeholder="Search incidents"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search incidents"
          />
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
          <label className="small check">
            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Closed
          </label>
        </div>
      </div>
      {error && <p className="banner bad">Cannot reach the OpsLoop API: {error}</p>}
      {!data && !error && <div className="skeleton" style={{ height: 300 }} />}
      {data && (
        <div className="board">
          {columns.map((col) => {
            const items = col.incidents.filter(visible);
            return (
              <div key={col.name} className={`column col-${col.name.toLowerCase().replaceAll(" ", "-")}`}>
                <header>
                  <span>{col.name}</span>
                  <span className="count">{items.length}</span>
                </header>
                <div className="cards">
                  {items.length === 0 && <p className="empty">Nothing here</p>}
                  {items.map((i) => (
                    <Link
                      key={i.id}
                      href={i.state === "AWAITING_APPROVAL" ? `/incidents/${i.id}?approve=1` : `/incidents/${i.id}`}
                      className={`card sev-edge-${i.severity} ${i.state === "AWAITING_APPROVAL" ? "needs-human" : ""}`}
                    >
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
                      {i.state === "AWAITING_APPROVAL" && <div className="card-cta">Review &amp; approve →</div>}
                    </Link>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
