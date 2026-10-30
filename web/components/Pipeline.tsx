import Link from "next/link";
import type { PipelineStage } from "@/lib/api";

const STAGE_HELP: Record<string, string> = {
  detect: "Watchers spot anomalies in metrics and logs",
  triage: "Dedupe, correlate across services, score severity",
  investigate: "Investigator writes Report 1 with cited evidence",
  fix: "Coding agent and a sector specialist write a patch",
  validate: "Sandbox replays the fault, runs visible and hidden tests",
  approve: "A human approves the exact validated version",
  release: "Staged rollout 5% → 25% → 100% with auto-rollback",
  judge: "Judge decides from measured before/after evidence",
  learn: "Learner tunes thresholds and knowledge (Stage 4)",
};

export function PipelineFlow({ stages, compact = false }: { stages: PipelineStage[]; compact?: boolean }) {
  return (
    <ol className={`pipeline ${compact ? "compact" : ""}`}>
      {stages.map((s, i) => (
        <li key={s.key} className={`stage ${s.count > 0 ? "busy" : ""} ${s.key === "approve" && s.count ? "needs-human" : ""}`}>
          <Link href={s.key === "approve" ? "/approvals" : "/incidents"} className="stage-box">
            <span className="stage-step">{i + 1}</span>
            <span className="stage-label">{s.label}</span>
            <span className="stage-count" aria-label={`${s.count} incidents`}>
              {s.count}
            </span>
            <span className="stage-owner">{s.owner}</span>
            {!compact && <span className="stage-help">{STAGE_HELP[s.key]}</span>}
          </Link>
        </li>
      ))}
    </ol>
  );
}

const STATE_ORDER = [
  ["OPEN", "TRIAGED"],
  ["INVESTIGATING", "REPORT1_READY"],
  ["FIXING"],
  ["VALIDATING"],
  ["AWAITING_APPROVAL"],
  ["DEPLOYING"],
  ["VERIFYING"],
  ["RESOLVED", "PARTIAL", "UNSUCCESSFUL", "INCONCLUSIVE", "ROLLED_BACK", "DISMISSED", "ESCALATED", "REJECTED"],
];
const STEP_LABELS = ["Triage", "Investigate", "Fix", "Validate", "Approve", "Release", "Judge", "Closed"];

/** Where one incident is in the loop. Closed states fill the whole bar. */
export function IncidentStepper({ state }: { state: string }) {
  const at = STATE_ORDER.findIndex((g) => g.includes(state));
  const bad = ["ESCALATED", "ROLLED_BACK", "UNSUCCESSFUL", "REJECTED"].includes(state);
  return (
    <ol className="stepper" aria-label={`Current state: ${state.replaceAll("_", " ")}`}>
      {STEP_LABELS.map((label, i) => {
        const cls = i < at ? "done" : i === at ? (bad ? "bad current" : "current") : "";
        return (
          <li key={label} className={cls}>
            <span className="step-dot">{i < at ? "✓" : i + 1}</span>
            <span className="step-label">{i === at && i === STEP_LABELS.length - 1 ? state.replaceAll("_", " ").toLowerCase() : label}</span>
          </li>
        );
      })}
    </ol>
  );
}
