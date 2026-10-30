import type { Severity, StreamEvent } from "./api";

export type Toast = {
  id: number;
  severity: Severity;
  title: string;
  body: string;
  href: string;
  sticky: boolean;
};

const STATE_TOASTS: Record<string, string> = {
  ESCALATED: "Escalated to a human",
  ROLLED_BACK: "Release rolled back",
  RESOLVED: "Verdict: resolved",
  PARTIAL: "Verdict: partially resolved",
  UNSUCCESSFUL: "Verdict: unsuccessful",
  INCONCLUSIVE: "Verdict: inconclusive",
  DISMISSED: "Dismissed",
};

// Needs a person: stays on screen for S1/S2 until dismissed.
const ACTION_NEEDED = new Set(["opened", "approval_requested", "ESCALATED", "ROLLED_BACK"]);

export function toastFor(e: StreamEvent): Toast | null {
  const inc = e.incident;
  let title: string | null = null;
  let trigger = e.kind;
  if (e.kind === "opened") title = `New ${inc.severity} incident`;
  else if (e.kind === "report_ready") title = "Report 1 ready";
  else if (e.kind === "approval_requested") {
    title = "Approval needed";
    trigger = "approval_requested";
  }
  else if (e.kind === "severity_changed") title = `Severity now ${inc.severity}`;
  else if (e.kind === "state_change" && e.to_state && STATE_TOASTS[e.to_state]) {
    title = STATE_TOASTS[e.to_state];
    trigger = e.to_state;
  }
  if (!title) return null;
  const urgent = inc.severity === "S1" || inc.severity === "S2";
  return {
    id: e.event_id,
    severity: inc.severity,
    title,
    body: `${inc.key} · ${inc.title}`,
    href: e.kind === "approval_requested" ? `/incidents/${inc.id}?approve=1` : `/incidents/${inc.id}`,
    sticky: urgent && ACTION_NEEDED.has(trigger),
  };
}
