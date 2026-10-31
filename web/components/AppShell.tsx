"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { apiBase, DEFAULT_API_BASE, discoverBackend, setApiBase, type Overview } from "@/lib/api";
import { useAuth } from "./Auth";
import { Icon } from "./Icon";
import { NotificationList, useLive } from "./Live";
import { StatusBar } from "./StatusBar";
import { useApi } from "./useApi";

type NavItem = { href: string; label: string; icon: string; badge?: (o: Overview) => number; urgent?: boolean };

const NAV: NavItem[] = [
  { href: "/", label: "Command Center", icon: "command" },
  { href: "/incidents", label: "Incident Board", icon: "board", badge: (o) => o.incidents.active },
  {
    href: "/approvals",
    label: "Approvals",
    icon: "shield",
    badge: (o) => o.approvals_pending,
    urgent: true,
  },
  { href: "/departments", label: "Departments", icon: "building" },
  { href: "/agents", label: "Agents & Pipeline", icon: "cpu" },
  { href: "/releases", label: "Releases & Judge", icon: "rocket" },
  { href: "/ledger", label: "Evidence Ledger", icon: "ledger" },
  { href: "/simulation", label: "Simulation Lab", icon: "flask" },
];

function isActive(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname() ?? "/";
  const [navOpen, setNavOpen] = useState(false);
  const { data: overview, error: overviewError } = useApi<Overview>("/overview", 15000);
  const offline = Boolean(overviewError);

  useEffect(() => setNavOpen(false), [path]);

  // While the backend is unreachable, look for a newly published address and switch to it.
  useEffect(() => {
    if (!offline) return;
    const before = apiBase();
    const id = setInterval(async () => {
      await discoverBackend();
      if (apiBase() !== before) window.location.reload();
    }, 20000);
    return () => clearInterval(id);
  }, [offline]);

  return (
    <div className={`shell ${navOpen ? "nav-open" : ""}`}>
      <aside className="sidebar" aria-label="Main navigation">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden>
            <svg viewBox="0 0 32 32" width="28" height="28">
              <circle cx="16" cy="16" r="11" fill="none" stroke="currentColor" strokeWidth="3" strokeDasharray="52 18" />
              <path d="M24 6l3 6-6.5.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span>
            OpsLoop
            <small>Autonomous IT Ops</small>
          </span>
        </Link>
        <nav>
          {NAV.map((n) => {
            const count = overview && n.badge ? n.badge(overview) : 0;
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`nav-item ${isActive(path, n.href) ? "active" : ""}`}
                aria-current={isActive(path, n.href) ? "page" : undefined}
              >
                <Icon name={n.icon} />
                <span>{n.label}</span>
                {count > 0 && <span className={`nav-badge ${n.urgent ? "urgent" : ""}`}>{count}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <p>
            <strong>Autonomous investigation and fix proposal, human-gated release.</strong>
          </p>
          <p className="muted">
            TaskFlow, its tenants, traffic and faults are simulated. Nothing ships without a human
            approving the exact version.
          </p>
        </div>
      </aside>
      <button className="scrim" aria-label="Close menu" onClick={() => setNavOpen(false)} tabIndex={-1} />
      <div className="frame">
        <header className="topbar">
          <button className="icon-btn menu-btn" aria-label="Open menu" onClick={() => setNavOpen(true)}>
            <Icon name="menu" />
          </button>
          <span className="pill sim" title="TaskFlow, its tenants, traffic and faults are all simulated">
            Simulated environment
          </span>
          <StatusBar />
          <Bell />
          <Account />
        </header>
        <main>
          {offline && (
            <div className="banner bad offline-banner" role="alert">
              <Icon name="alert" size={22} />
              <div>
                <strong>The OpsLoop backend is offline.</strong>
                <span className="muted">
                  It runs on the host laptop. Start it there with <strong>Start OpsLoop</strong>; this page
                  reconnects by itself within about 20 seconds.
                  {overview ? " Figures below are from the last successful update." : ""}
                </span>
              </div>
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

function Bell() {
  const { unread, markRead } = useLive();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  return (
    <div className="menu-wrap" ref={ref}>
      <button
        className="icon-btn"
        aria-label={`Notifications${unread ? `, ${unread} new` : ""}`}
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          markRead();
        }}
      >
        <Icon name="bell" />
        {unread > 0 && <span className="dot-badge">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="popover notif-pop" role="dialog" aria-label="Live notifications">
          <header>Live notifications</header>
          <NotificationList onPick={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}

function Account() {
  const { me, signIn, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useOutside(open, () => setOpen(false));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(value);
      setValue("");
      setOpen(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="menu-wrap" ref={ref}>
      <button className={`account-btn ${me ? "signed" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
        <Icon name="user" />
        <span>{me ? me.name : "Sign in"}</span>
        {me && <span className={`role role-${me.role}`}>{me.role}</span>}
      </button>
      {open && (
        <div className="popover account-pop" role="dialog" aria-label="Account">
          {me ? (
            <>
              <p>
                Signed in as <strong>{me.name}</strong> ({me.role}).
              </p>
              <p className="muted small">
                {me.role === "approver"
                  ? "You can approve, modify or reject validated fixes and drive the simulation."
                  : "Viewers can see everything but cannot approve or change the simulation."}
              </p>
              <button className="btn" onClick={signOut}>
                Sign out
              </button>
            </>
          ) : (
            <form onSubmit={submit}>
              <label className="field">
                Access token
                <input
                  type="password"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder="approver or viewer token"
                  autoComplete="off"
                />
              </label>
              {error && <p className="error small">{error}</p>}
              <button className="btn primary" disabled={busy || !value.trim()}>
                Sign in
              </button>
              <p className="muted small">Approvals are bound to the person and the exact version hash.</p>
            </form>
          )}
          <ApiSetting />
        </div>
      )}
    </div>
  );
}

/** Point this browser at a different OpsLoop API, e.g. after the tunnel restarts with a new URL. */
function ApiSetting() {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setValue(apiBase()), []);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const url = value.trim().replace(/\/$/, "");
    if (url && !/^https:\/\/|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(url)) {
      setError("Use an https:// address (or http://localhost).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${url || DEFAULT_API_BASE}/health`, { cache: "no-store" });
      if (!res.ok) throw new Error(`health check returned HTTP ${res.status}`);
      setApiBase(url);
      window.location.reload();
    } catch (err) {
      setError(`Cannot reach that API: ${(err as Error).message}`);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="api-setting">
      <label className="field">
        OpsLoop API address
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={DEFAULT_API_BASE} spellCheck={false} />
      </label>
      {error && <p className="error small">{error}</p>}
      <div className="buttons">
        <button className="btn sm" disabled={busy}>
          Test &amp; connect
        </button>
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            setApiBase("");
            window.location.reload();
          }}
        >
          Reset to default
        </button>
      </div>
    </form>
  );
}
