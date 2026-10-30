"use client";

import { useEffect, useState } from "react";
import { ago, getJson, type WatcherStatus } from "@/lib/api";
import { useLive } from "./Live";

type Verify = { ok: boolean; checked: number; first_bad_id: number | null };

export function StatusBar() {
  const { connected } = useLive();
  const [watchers, setWatchers] = useState<WatcherStatus[]>([]);
  const [ledger, setLedger] = useState<Verify | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const load = () => {
      getJson<WatcherStatus[]>("/watchers").then(setWatchers).catch(() => setWatchers([]));
      getJson<Verify>("/ledger/verify").then(setLedger).catch(() => setLedger(null));
      setNow(Date.now());
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="status">
      <span className={`pill ${connected ? "ok" : "bad"}`}>{connected ? "Live" : "Reconnecting"}</span>
      {watchers.map((w) => {
        const stale = now - new Date(w.last_seen).getTime() > 30000;
        return (
          <span key={w.watcher} className={`pill ${stale ? "bad" : "ok"}`} title={`last seen ${ago(w.last_seen, now)} ago`}>
            {w.watcher} v{w.config_version}
          </span>
        );
      })}
      {ledger && (
        <span className={`pill ${ledger.ok ? "ok" : "bad"}`} title={`${ledger.checked} entries checked`}>
          Ledger {ledger.ok ? "verified" : `broken at #${ledger.first_bad_id}`}
        </span>
      )}
    </div>
  );
}
