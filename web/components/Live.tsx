"use client";

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { API_BASE, type StreamEvent } from "@/lib/api";
import { toastFor, type Toast } from "@/lib/toasts";

type LiveState = { tick: number; connected: boolean };
const LiveContext = createContext<LiveState>({ tick: 0, connected: false });

export function useLive(): LiveState {
  return useContext(LiveContext);
}

const AUTO_DISMISS_MS = 6000;
const MAX_TOASTS = 5;

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const [tick, setTick] = useState(0);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  useEffect(() => {
    const es = new EventSource(`${API_BASE}/events/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("incident", (msg) => {
      const event = JSON.parse((msg as MessageEvent<string>).data) as StreamEvent;
      setTick((n) => n + 1);
      const toast = toastFor(event);
      if (!toast) return;
      setToasts((t) => [toast, ...t.filter((x) => x.id !== toast.id)].slice(0, MAX_TOASTS));
      if (!toast.sticky) {
        timers.current.set(toast.id, setTimeout(() => dismiss(toast.id), AUTO_DISMISS_MS));
      }
    });
    const pending = timers.current;
    return () => {
      es.close();
      pending.forEach(clearTimeout);
    };
  }, [dismiss]);

  return (
    <LiveContext.Provider value={{ tick, connected }}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast sev-${t.severity}`}>
            <Link href={t.href} className="toast-body" onClick={() => dismiss(t.id)}>
              <span className={`sev sev-${t.severity}`}>{t.severity}</span>
              <span>
                <strong>{t.title}</strong>
                <span className="toast-text">{t.body}</span>
              </span>
            </Link>
            <button className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </LiveContext.Provider>
  );
}

/** Re-run `load` on mount and (debounced) whenever a live event arrives. */
export function useLiveReload(load: () => void, debounceMs = 400): void {
  const { tick } = useLive();
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    const t = setTimeout(() => loadRef.current(), tick === 0 ? 0 : debounceMs);
    return () => clearTimeout(t);
  }, [tick, debounceMs]);
}
