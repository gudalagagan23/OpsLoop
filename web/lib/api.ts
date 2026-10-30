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
