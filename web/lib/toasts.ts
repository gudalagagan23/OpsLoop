import type { Severity, StreamEvent } from "./api";

export type ToastTone = "new" | "info" | "approval" | "alert" | "good";

export type Toast = {
  id: number;
  severity: Severity;
  tone: ToastTone;
  title: string;
  body: string;
  wing: string;
  href: string;
  action: string;
  sticky: boolean;
  ts: string;
};

const STATE_TOASTS: Record<string, [string, ToastTone]> = {
  INVESTIGATING: ["Investigator started", "info"],
  FIXING: ["Coding agent is writing a fix", "info"],
  VALIDATING: ["Fix is being validated in the sandbox", "info"],
  DEPLOYING: ["Staged rollout started", "info"],
  VERIFYING: ["Judge is verifying the release", "info"],
  ESCALATED: ["Escalated to a human", "alert"],
  ROLLED_BACK: ["Release rolled back", "alert"],
  RESOLVED: ["Verdict: resolved", "good"],
  PARTIAL: ["Verdict: partially resolved", "alert"],
  UNSUCCESSFUL: ["Verdict: unsuccessful", "alert"],
  INCONCLUSIVE: ["Verdict: inconclusive", "info"],
  DISMISSED: ["Incident cleared", "good"],
};

// Needs a person: stays on screen for S1/S2 until dismissed.
const ACTION_NEEDED = new Set(["opened", "approval_requested", "ESCALATED", "ROLLED_BACK"]);

export function toastFor(e: StreamEvent): Toast | null {
  const inc = e.incident;
  let title: string | null = null;
  let tone: ToastTone = "info";
  let trigger = e.kind;
  let action = "Open";
  if (e.kind === "opened") {
    title = `New ${inc.severity} incident`;
    tone = "new";
  } else if (e.kind === "report_ready") {
    title = "Report 1 ready: root cause found";
  } else if (e.kind === "approval_requested") {
    title = "Approval needed";
    tone = "approval";
    action = "Review now";
  } else if (e.kind === "severity_changed") {
    title = `Severity now ${inc.severity}`;
    tone = "alert";
  } else if (e.kind === "state_change" && e.to_state && STATE_TOASTS[e.to_state]) {
    [title, tone] = STATE_TOASTS[e.to_state];
    trigger = e.to_state;
  }
  if (!title) return null;
  const urgent = inc.severity === "S1" || inc.severity === "S2";
  return {
    id: e.event_id,
    severity: inc.severity,
    tone,
    title,
    body: `${inc.key} · ${inc.title}`,
    wing: inc.wing,
    href: e.kind === "approval_requested" ? `/incidents/${inc.id}?approve=1` : `/incidents/${inc.id}`,
    action,
    sticky: urgent && ACTION_NEEDED.has(trigger),
    ts: e.ts,
  };
}
