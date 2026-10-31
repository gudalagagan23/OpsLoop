import { eventText, fmtMetric } from "@/components/ReleasePanel";
import { WING_LABEL, type IncidentDetail } from "./api";

const line = (k: string, v: unknown) => `| ${k} | ${String(v ?? "–").replaceAll("|", "\\|").replaceAll("\n", " ")} |`;

/** A self-contained Markdown report of one incident: both reports, timeline and cited evidence. */
export function incidentMarkdown(inc: IncidentDetail): string {
  const r1 = inc.report1?.body;
  const r2 = inc.report2?.body;
  const out: string[] = [];
  out.push(`# ${inc.key}: ${inc.title}`, "");
  out.push(
    "> OpsLoop incident report. Simulated environment: autonomous investigation and fix proposal, human-gated release.",
    "",
  );
  out.push("| Field | Value |", "|---|---|");
  out.push(
    line("Severity", inc.severity),
    line("Department", WING_LABEL[inc.wing] ?? inc.wing),
    line("Service", inc.service),
    line("State", inc.state),
    line("First signal", inc.first_signal_at),
    line("Signals", inc.signal_count),
    line("Closed", inc.closed_at ? `${inc.closed_at} (${inc.closed_reason})` : "open"),
    line("Generated", new Date().toISOString()),
    "",
  );

  out.push("## Report 1: Investigation", "");
  if (!r1) out.push("_Not ready yet._", "");
  else {
    if (inc.report1?.is_stub) out.push("_Stage 1 stub report (no model configured)._", "");
    out.push(r1.summary, "");
    out.push(
      `**Suspected root cause:** ${r1.suspected_root_cause.description} (${r1.suspected_root_cause.category}, confidence ${r1.suspected_root_cause.confidence.toFixed(2)})`,
      "",
      `**Affected services:** ${r1.affected_services.join(", ") || "–"}`,
      "",
      `**Deployment correlation:** ${r1.deployment_correlation.relation}`,
      "",
      `**Evidence for:** ${r1.evidence_for.map((e) => `#${e}`).join(", ") || "–"}  `,
      `**Evidence against:** ${r1.evidence_against.map((e) => `#${e}`).join(", ") || "–"}`,
      "",
      `**Uncertainty:** ${r1.uncertainty}`,
      "",
      "**Recommended strategies:**",
      "",
      ...r1.recommended_strategies.map((s) => `- **${s.strategy}** (${s.risk} risk): ${s.rationale}`),
      "",
    );
  }

  out.push("## Report 2: Validation", "");
  if (!r2) out.push("_No validated fix yet._", "");
  else {
    out.push(`**Verdict:** ${r2.verdict} · **Strategy:** ${r2.strategy} · **Scenario:** ${r2.scenario}`, "");
    out.push(`**Version hash:** \`${r2.version_hash}\` (base \`${r2.base_commit}\`)`, "");
    out.push("| Step | Result | Time | Detail |", "|---|---|---|---|");
    r2.tests.forEach((t) => out.push(`| ${t.step} | ${t.status} | ${t.seconds}s | ${t.summary.replaceAll("|", "\\|")} |`));
    out.push("");
    if (r2.performance) {
      out.push("| Acceptance criterion | Before | After | Required | Met |", "|---|---|---|---|---|");
      r2.performance.criteria.forEach((c) =>
        out.push(`| ${c.label} | ${r2.performance?.before[c.metric] ?? "–"} | ${c.actual} | ${c.op} ${c.threshold} | ${c.passed ? "yes" : "no"} |`),
      );
      out.push("");
    }
    out.push("**Security findings:** " + (r2.security_findings.length ? "" : "none"), "");
    r2.security_findings.forEach((f) => out.push(`- ${f.rule}: ${f.detail}`));
    out.push("", "**Remaining risks:**", "", ...r2.remaining_risks.map((x) => `- ${x}`), "");
    out.push(`**Rollback plan:** ${r2.rollback_plan}`, "");
    out.push("```diff", r2.diff.trimEnd(), "```", "");
  }

  if (inc.attempts.length) {
    out.push("## Fix attempts", "", "| # | Strategy | Specialist | Status | Version | Cost |", "|---|---|---|---|---|---|");
    inc.attempts.forEach((a) =>
      out.push(`| ${a.attempt_no} | ${a.strategy} | ${a.specialist} | ${a.status} | ${a.version_hash?.slice(0, 12) ?? "–"} | $${a.cost_usd.toFixed(2)} |`),
    );
    out.push("");
  }

  out.push("## Approval", "");
  if (inc.approval.decisions.length === 0) out.push("_No decision recorded._", "");
  inc.approval.decisions.forEach((d) =>
    out.push(`- ${d.created_at}: **${d.decision}** by ${d.approver} for \`${d.version_hash.slice(0, 16)}\`${d.reason ? `: ${d.reason}` : ""}`),
  );
  out.push("");

  out.push("## Release and verdict", "");
  const rel = inc.release;
  if (!rel || (rel.deployments.length === 0 && !rel.outcome)) out.push("_Not released yet._", "");
  rel?.deployments.forEach((d) => {
    out.push(
      `Staged rollout of \`${d.version_hash.slice(0, 16)}\`: **${d.state}**, reached ${d.stage_pct}% (started ${d.started_at}).`,
      "",
      ...d.events.map((e) => `- ${new Date(e.t * 1000).toISOString()}: ${eventText(e)}`),
      "",
    );
  });
  if (rel?.outcome) {
    const o = rel.outcome;
    out.push(`**Judge verdict: ${o.verdict}** (${o.mode}). ${o.explanation ?? ""}`, "");
    if (o.summary.effects?.length) {
      out.push("| Metric | Control | Fix | 95% CI of improvement |", "|---|---|---|---|");
      o.summary.effects.forEach((e) =>
        out.push(
          `| ${e.metric} | ${fmtMetric(e.metric, e.control)} | ${fmtMetric(e.metric, e.candidate)} | ${e.ci_low === null || e.ci_high === null ? "–" : `${fmtMetric(e.metric, e.ci_low)} to ${fmtMetric(e.metric, e.ci_high)}`} |`,
        ),
      );
      out.push("");
    }
  }

  out.push("## Timeline", "", "| Time | Event | By | Note |", "|---|---|---|---|");
  inc.events.forEach((e) =>
    out.push(
      `| ${e.ts} | ${e.kind === "state_change" ? `${e.from_state} → ${e.to_state}` : e.kind} | ${e.actor} | ${(e.reason ?? "").replaceAll("|", "\\|")} |`,
    ),
  );
  out.push("");

  out.push("## Signals", "", "| Signal | Severity | Value | Baseline | Fired | Cleared |", "|---|---|---|---|---|---|");
  inc.signals.forEach((s) =>
    out.push(`| ${s.signal_id} | ${s.severity} | ${s.value} | ${s.baseline ?? "–"} | ${s.fired_at} | ${s.cleared_at ?? "firing"} |`),
  );
  out.push("");

  out.push("## Evidence ledger (hash chain)", "");
  inc.evidence.forEach((e) => {
    out.push(`### #${e.id} · ${e.type} · ${e.source}`, "", `- time: ${e.ts}`, `- prev_hash: \`${e.prev_hash}\``, `- hash: \`${e.hash}\``, "");
    out.push("```json", JSON.stringify(e.payload, null, 2), "```", "");
  });
  return out.join("\n");
}

export function downloadText(filename: string, text: string, type = "text/markdown"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
