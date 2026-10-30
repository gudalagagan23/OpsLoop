export const API_BASE = process.env.NEXT_PUBLIC_OPSLOOP_API ?? "http://localhost:8000";

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
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
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
  meta: "OpsLoop",
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
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as { detail?: string } & T;
  if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
  return data;
}
