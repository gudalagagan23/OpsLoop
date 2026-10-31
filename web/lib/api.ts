export const DEFAULT_API_BASE = (process.env.NEXT_PUBLIC_OPSLOOP_API ?? "http://localhost:8000").replace(/\/$/, "");
const API_KEY = "opsloop.api";
// Where the start script publishes the backend's current public address (a quick tunnel gets a
// new URL every time it starts). Empty string turns discovery off.
const DISCOVERY_URL =
  process.env.NEXT_PUBLIC_OPSLOOP_DISCOVERY ??
  "https://api.github.com/repos/gudalagagan23/OpsLoop/contents/backend.json?ref=backend-url";
let discovered: string | null = null;

/**
 * The API this browser talks to: an address saved in Settings, else the one the start script
 * published, else the one baked in at build time.
 */
export function apiBase(): string {
  let saved: string | null = null;
  try {
    saved = window.localStorage.getItem(API_KEY);
  } catch {
    /* storage unavailable */
  }
  return saved || discovered || DEFAULT_API_BASE;
}

async function healthy(base: string, timeoutMs = 4000): Promise<boolean> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    return (await fetch(`${base}/health`, { cache: "no-store", signal: ctl.signal })).ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

/**
 * Find the backend before anything connects: read the published address, and drop a saved
 * address that no longer answers (yesterday's tunnel). Never throws.
 */
export async function discoverBackend(): Promise<void> {
  if (DISCOVERY_URL) {
    try {
      const res = await fetch(DISCOVERY_URL, {
        cache: "no-store",
        headers: { Accept: "application/vnd.github.raw+json" },
      });
      if (res.ok) {
        const body = (await res.json()) as { url?: string };
        if (body.url && /^https:\/\/[^/]+$/.test(body.url.replace(/\/$/, ""))) {
          discovered = body.url.replace(/\/$/, "");
        }
      }
    } catch {
      /* offline or rate-limited: fall back to the build-time address */
    }
  }
  let saved: string | null = null;
  try {
    saved = window.localStorage.getItem(API_KEY);
  } catch {
    return;
  }
  if (saved && saved !== discovered && !(await healthy(saved))) {
    try {
      window.localStorage.removeItem(API_KEY);
    } catch {
      /* ignore */
    }
  }
}

/** Save (or with "" clear) the API override. The saved token is dropped: it belongs to the old API. */
export function setApiBase(url: string): void {
  try {
    const clean = url.trim().replace(/\/$/, "");
    if (clean && clean !== DEFAULT_API_BASE) window.localStorage.setItem(API_KEY, clean);
    else window.localStorage.removeItem(API_KEY);
    window.localStorage.removeItem("opsloop.token");
  } catch {
    /* storage unavailable: the override just won't persist */
  }
}

export type Severity = "S1" | "S2" | "S3" | "S4";

export type IncidentSummary = {
  id: number;
  key: string;
  title: string;
  wing: string;
  service: string;
  severity: Severity;
  state: string;
  column: string;
  assignee: string | null;
  signal_count: number;
  created_at: string;
  updated_at: string;
  first_signal_at: string;
  closed_at: string | null;
  closed_reason: string | null;
};

export type BoardColumn = { name: string; states: string[]; incidents: IncidentSummary[] };
export type Board = { columns: BoardColumn[]; simulated: boolean };

export type StreamEvent = {
  event_id: number;
  kind: string;
  to_state: string | null;
  reason: string | null;
  ts: string;
  incident: IncidentSummary;
};

export type SignalRow = {
  id: number;
  signal_id: string;
  labels: Record<string, string>;
  service: string;
  severity: Severity;
  method: string;
  value: number;
  baseline: number | null;
  fired_at: string;
  cleared_at: string | null;
  evidence_id: number | null;
};

export type EventRow = {
  id: number;
  ts: string;
  kind: string;
  from_state: string | null;
  to_state: string | null;
  actor: string;
  reason: string | null;
  evidence_id: number | null;
};

export type EvidenceRow = {
  id: number;
  ts: string;
  type: string;
  source: string;
  payload: Record<string, unknown>;
  hash: string;
  prev_hash: string;
};

export type Report1 = {
  summary: string;
  suspected_root_cause: { description: string; category: string; confidence: number };
  evidence_for: number[];
  evidence_against: number[];
  affected_services: string[];
  deployment_correlation: { deploy_event_id: number | null; relation: string };
  uncertainty: string;
  recommended_strategies: { strategy: string; rationale: string; risk: string }[];
  stub?: boolean;
};

export type IncidentDetail = IncidentSummary & {
  signals: SignalRow[];
  events: EventRow[];
  evidence: EvidenceRow[];
  report1: { body: Report1; is_stub: boolean; created_at: string } | null;
  report2: { body: Report2; version: number; created_at: string } | null;
  attempts: Attempt[];
  approval: ApprovalInfo;
};

export type WatcherStatus = {
  watcher: string;
  last_seen: string;
  config_version: number;
  active_version: number | null;
};

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

export const WING_LABEL: Record<string, string> = {
  api: "API",
  database: "Database",
  auth: "Auth",
  billing: "Billing",
  infra: "Infra",
  ux: "User Exp.",
  meta: "Watcher health check",
};

// ---- Stage 2: Report 2, attempts, approval ---------------------------------------------------

export type Criterion = {
  metric: string;
  op: string;
  threshold: number;
  actual: number;
  passed: boolean;
  label: string;
};

export type Report2 = {
  verdict: string;
  strategy: string;
  scenario: string;
  diff: string;
  tests: { step: string; status: string; seconds: number; summary: string }[];
  performance: {
    before: Record<string, number>;
    after: Record<string, number>;
    criteria: Criterion[];
    gap_closed: number | null;
  } | null;
  security_findings: { rule: string; file?: string; detail: string }[];
  remaining_risks: string[];
  rollback_plan: string;
  version_hash: string;
  base_commit: string;
};

export type Attempt = {
  attempt_no: number;
  strategy: string;
  specialist: string;
  status: string;
  version_hash: string | null;
  cost_usd: number;
  notes: string | null;
};

export type Decision = {
  id: number;
  decision: string;
  approver: string;
  version_hash: string;
  reason: string | null;
  follow_up: string | null;
  created_at: string;
  expires_at: string | null;
};

export type ApprovalInfo = { pending_version_hash: string | null; decisions: Decision[] };

// ---- dashboard views ---------------------------------------------------------------------------

export type PipelineStage = { key: string; label: string; owner: string; count: number };

export type Overview = {
  simulated: boolean;
  now: string;
  incidents: {
    active: number;
    by_severity: Record<Severity, number>;
    by_stage: Record<string, number>;
    opened_24h: number;
    closed_24h: Record<string, number>;
  };
  approvals_pending: number;
  escalated: number;
  fixes: { attempts: number; validated: number; pass_rate: number | null };
  timings: { median_signal_to_report1_s: number | null; median_signal_to_approval_s: number | null };
  llm: { calls_24h: number; tokens_24h: number; cost_usd_24h: number; model: string | null };
  ledger_entries: number;
  knowledge_chunks: number;
  pipeline: PipelineStage[];
};

export type Department = {
  wing: string;
  name: string;
  services: string[];
  description: string;
  status: "incident" | "healthy" | "stale" | "not_monitored";
  monitored: boolean;
  watcher: { last_seen: string; seconds_ago: number; config_version: number } | null;
  signals: { id: string; title: string; method: string; severity: Record<string, string> }[];
  specialist: string | null;
  active_incidents: IncidentSummary[];
  incidents_24h: number;
  planned_stage: string | null;
};

export type ComponentStatus = "active" | "working" | "ready" | "waiting" | "down" | "planned";

export type AgentComponent = {
  key: string;
  name: string;
  role: string;
  status: ComponentStatus;
  detail: string;
  last_activity: string | null;
  working_on?: number | null;
};

export type AgentsInfo = {
  components: AgentComponent[];
  jobs: { queue: string; state: string; count: number }[];
  llm: { model: string; agent: string; ts: string; latency_s: number } | null;
};

export type LiveMetrics = {
  available: boolean;
  step_s: number;
  series: Record<string, [number, number][]>;
};

export type ActivityRow = StreamEvent & { from_state: string | null; actor: string };

export type DecisionRates = {
  total: number;
  counts: Record<string, number>;
  rates: Record<string, number>;
};

export type PendingApproval = IncidentSummary & {
  version_hash: string | null;
  specialist: string | null;
  waiting_since: string | null;
};

export type ApprovalsQueue = {
  pending: PendingApproval[];
  history: (Omit<Decision, "id"> & { id: number; incident: IncidentSummary })[];
  rates: DecisionRates;
};

export type LedgerEntry = EvidenceRow & { incident_id: number | null };

export type LlmCallRow = {
  id: number;
  ts: string;
  agent: string;
  model: string;
  incident_id: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_s: number;
};

export type Releases = {
  deployments: {
    id: number;
    incident_id: number;
    version_hash: string;
    kind: string;
    state: string;
    stage_pct: number;
    started_at: string;
    finished_at: string | null;
    reasons: unknown;
  }[];
  outcomes: {
    id: number;
    incident_id: number;
    verdict: string;
    mode: string;
    summary: string | null;
    explanation: string | null;
    created_at: string;
  }[];
};

export type RecentSignal = {
  id: number;
  watcher: string;
  signal_id: string;
  labels: Record<string, string>;
  severity: Severity;
  value: number;
  baseline: number | null;
  fired_at: string;
  cleared_at: string | null;
  incident_id: number | null;
};

export type Fault = { id: string; wing: string; title: string; on: boolean };
export type Me = { name: string; role: string };

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export async function getAuthJson<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = (await res.json().catch(() => ({}))) as { detail?: string } & T;
  if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
  return data;
}

const TOKEN_KEY = "opsloop.token";

export function getToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage unavailable (private window): the token just won't persist */
  }
}

export async function postJson<T>(path: string, body: unknown, token: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as { detail?: string } & T;
  if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
  return data;
}
