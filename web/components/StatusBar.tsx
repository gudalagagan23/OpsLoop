"use client";

import { ago, type WatcherStatus } from "@/lib/api";
import { useLive } from "./Live";
import { useApi, useNow } from "./useApi";

type Verify = { ok: boolean; checked: number; first_bad_id: number | null };

export function StatusBar() {
  const { connected } = useLive();
  const watchers = useApi<WatcherStatus[]>("/watchers", 10000);
  const ledger = useApi<Verify>("/ledger/verify", 30000);
  const now = useNow(5000);
  const stale = (watchers.data ?? []).filter((w) => now - new Date(w.last_seen).getTime() > 30000);

  return (
    <div className="status">
      <span className={`pill ${connected ? "ok live" : "bad"}`} title="Server-sent event stream">
        {connected ? "Live" : "Reconnecting"}
      </span>
      {watchers.data && (
        <span
          className={`pill ${stale.length ? "bad" : "ok"}`}
          title={watchers.data
            .map((w) => `${w.watcher} watcher v${w.config_version}, last seen ${ago(w.last_seen, now)} ago`)
            .join("\n")}
        >
          Watcher health check: {watchers.data.length - stale.length}/{watchers.data.length} reporting
        </span>
      )}
      {ledger.data && (
        <span className={`pill ${ledger.data.ok ? "ok" : "bad"} hide-sm`} title={`${ledger.data.checked} entries checked`}>
          Ledger {ledger.data.ok ? "verified" : `broken at #${ledger.data.first_bad_id}`}
        </span>
      )}
      {(watchers.error || ledger.error) && <span className="pill bad">API unreachable</span>}
    </div>
  );
}
