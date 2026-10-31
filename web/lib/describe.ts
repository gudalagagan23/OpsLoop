import type { ActivityRow } from "./api";

const STATE_TEXT: Record<string, string> = {
  TRIAGED: "triaged",
  INVESTIGATING: "investigation started",
  REPORT1_READY: "Report 1 ready",
  FIXING: "fix in progress",
  VALIDATING: "validating in sandbox",
  AWAITING_APPROVAL: "waiting for human approval",
  APPROVED: "approved by a human; release queued",
  DEPLOYING: "staged rollout",
  VERIFYING: "Judge verifying",
  RESOLVED: "resolved",
  PARTIAL: "partially resolved",
  UNSUCCESSFUL: "unsuccessful",
  INCONCLUSIVE: "inconclusive",
  ROLLED_BACK: "rolled back",
  ESCALATED: "escalated to a human",
  DISMISSED: "cleared (signals recovered)",
  REJECTED: "rejected",
};

export function describe(a: ActivityRow): string {
  switch (a.kind) {
    case "opened":
      return "Incident opened";
    case "signal_attached":
      return "New signal correlated";
    case "release_progress":
      return a.reason ? `Release: ${a.reason}` : "Release progress";
    case "learned":
      return "Learner added the fix to the knowledge base";
    case "signal_escalated":
      return "Stronger signal raised the severity";
    case "severity_changed":
      return `Severity changed to ${a.incident.severity}`;
    case "report_ready":
      return "Report 1 published";
    case "approval_requested":
      return "Approval requested";
    case "approved":
    case "approval":
      return "Fix approved";
    case "state_change":
      return a.to_state
        ? (STATE_TEXT[a.to_state] ?? a.to_state.replaceAll("_", " ").toLowerCase())
        : "state changed";
    default:
      return a.kind.replaceAll("_", " ");
  }
}

export function toneOf(a: ActivityRow): "bad" | "good" | "warn" | "info" {
  if (a.kind === "opened" || a.to_state === "ESCALATED" || a.to_state === "ROLLED_BACK") return "bad";
  if (a.kind === "approval_requested" || a.to_state === "AWAITING_APPROVAL") return "warn";
  if (a.to_state === "RESOLVED" || a.to_state === "DISMISSED" || a.kind === "learned") return "good";
  if (a.kind === "release_progress" && /rolled back/i.test(a.reason ?? "")) return "bad";
  return "info";
}

export const ACTOR_LABEL: Record<string, string> = {
  engine: "Watchers",
  "incident-manager": "Incident Manager",
  investigator: "Investigator",
  "investigator-stub": "Investigator (stub)",
  orchestrator: "Coding agent",
  testloop: "Test Loop",
  "test-loop": "Test Loop",
  heartbeat: "Watcher health check",
  "replay-harness": "Replay harness",
  operator: "Operator",
  judge: "Judge",
  deploy: "Deployment",
  deployment: "Deployment controller",
  learner: "Learner",
  "verification-run": "Verification run",
};
