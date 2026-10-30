"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { API_BASE, WING_LABEL, type StreamEvent } from "@/lib/api";
import { toastFor, type Toast } from "@/lib/toasts";
import { Icon, TONE_ICON } from "./Icon";

type LiveState = {
  tick: number;
  connected: boolean;
  notifications: Toast[];
  unread: number;
  markRead: () => void;
};

const LiveContext = createContext<LiveState>({
  tick: 0,
  connected: false,
  notifications: [],
  unread: 0,
  markRead: () => {},
});

export function useLive(): LiveState {
  return useContext(LiveContext);
}

const AUTO_DISMISS_MS = 7000;
const MAX_TOASTS = 4;
const MAX_HISTORY = 30;

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [tick, setTick] = useState(0);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [notifications, setNotifications] = useState<Toast[]>([]);
  const [unread, setUnread] = useState(0);
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
      setNotifications((n) => [toast, ...n.filter((x) => x.id !== toast.id)].slice(0, MAX_HISTORY));
      setUnread((u) => u + 1);
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

  const markRead = useCallback(() => setUnread(0), []);

  return (
    <LiveContext.Provider value={{ tick, connected, notifications, unread, markRead }}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast tone-${t.tone} sev-${t.severity}`}>
            <span className="toast-icon" aria-hidden>
              <Icon name={TONE_ICON[t.tone]} />
            </span>
            <div className="toast-main">
              <div className="toast-title">
                <strong>{t.title}</strong>
                <span className={`sev sev-${t.severity}`}>{t.severity}</span>
              </div>
              <span className="toast-text">{t.body}</span>
              <div className="toast-foot">
                <span className="muted">{WING_LABEL[t.wing] ?? t.wing}</span>
                <button
                  className={`btn sm ${t.tone === "approval" ? "approve" : ""}`}
                  onClick={() => {
                    dismiss(t.id);
                    router.push(t.href);
                  }}
                >
                  {t.action}
                </button>
              </div>
            </div>
            <button className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
            {!t.sticky && <span className="toast-timer" style={{ animationDuration: `${AUTO_DISMISS_MS}ms` }} />}
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

export function NotificationList({ onPick }: { onPick: () => void }) {
  const { notifications } = useLive();
  if (notifications.length === 0)
    return <p className="muted pad">No events yet in this session. They appear here live.</p>;
  return (
    <ul className="notif-list">
      {notifications.map((n) => (
        <li key={n.id}>
          <Link href={n.href} onClick={onPick} className={`notif tone-${n.tone}`}>
            <span className="toast-icon" aria-hidden>
              <Icon name={TONE_ICON[n.tone]} />
            </span>
            <span className="notif-main">
              <strong>{n.title}</strong>
              <span className="toast-text">{n.body}</span>
            </span>
            <span className="muted small">{new Date(n.ts).toLocaleTimeString()}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
