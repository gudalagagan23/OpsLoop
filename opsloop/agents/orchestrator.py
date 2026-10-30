"""Main Coding Agent (orchestrator) decision logic. Pure functions, no I/O, so every budget and
routing rule is unit-testable. The LangGraph workflow in opsloop/workflows/remediation.py wires
these decisions to the sandbox, Test Loop and database."""

from dataclasses import dataclass
from typing import Any

from opsloop.agents.profiles import SpecialistProfile
from opsloop.testloop.loop import Validation

NO_IMPROVEMENT_GAP = 0.10  # spec section 10: < 10% of the gap closed = "no improvement"
NOTE_LIMIT = 1200


@dataclass(frozen=True)
class Budgets:
    max_attempts: int = 4
    max_switches: int = 2
    time_s: float = 1200.0
    cost_usd: float = 5.0


@dataclass(frozen=True)
class Decision:
    action: str  # accept | retry | switch | escalate
    reason: str
    specialist: str | None = None


def choose_strategy(
    report: dict[str, Any], escalation_confidence: float, exclude: tuple[str, ...] = ()
) -> tuple[str, str]:
    """Pick the remediation strategy from Report 1, skipping any a human rejected.
    Returns (strategy, reason)."""
    confidence = report["suspected_root_cause"]["confidence"]
    if confidence < escalation_confidence:
        return "escalate", f"confidence {confidence:.2f} is below {escalation_confidence}"
    recs = [r["strategy"] for r in report["recommended_strategies"] if r["strategy"] not in exclude]
    likely_deploy = report["deployment_correlation"]["relation"] == "likely"
    if "rollback" in recs and likely_deploy:
        return "rollback", "a recent deploy correlates strongly with the incident"
    for strategy in recs:
        if strategy != "escalate":
            return strategy, f"recommended by the Investigator (confidence {confidence:.2f})"
    return "escalate", "the Investigator recommends escalation"


def next_specialist(
    current: str, category_specialist: str, tried: list[str], profiles: dict[str, SpecialistProfile]
) -> str | None:
    """Switch order: the category's specialist first, then the generic one, then none."""
    for candidate in (category_specialist, "generic_specialist"):
        if candidate not in tried and candidate != current and candidate in profiles:
            return candidate
    return None


def decide(
    *,
    verdict: str,
    gap_closed: float,
    attempt_no: int,
    switches: int,
    elapsed_s: float,
    cost_usd: float,
    budgets: Budgets,
    current_specialist: str,
    category_specialist: str,
    tried: list[str],
    profiles: dict[str, SpecialistProfile],
) -> Decision:
    if verdict == "passed":
        return Decision("accept", "validation passed")
    if verdict == "not_reproduced":
        return Decision("escalate", "the incident could not be reproduced in the sandbox")
    if cost_usd >= budgets.cost_usd:
        return Decision("escalate", f"cost budget ${budgets.cost_usd:.2f} exhausted")
    if elapsed_s >= budgets.time_s:
        return Decision("escalate", f"time budget {budgets.time_s:.0f}s exhausted")
    if attempt_no >= budgets.max_attempts:
        return Decision("escalate", f"retry budget of {budgets.max_attempts} attempts exhausted")
    mechanical = verdict in {"rejected_by_policy", "patch_does_not_apply", "agent_failed"}
    if not mechanical and gap_closed < NO_IMPROVEMENT_GAP and switches < budgets.max_switches:
        nxt = next_specialist(current_specialist, category_specialist, tried, profiles)
        if nxt:
            return Decision(
                "switch",
                f"no improvement (closed {gap_closed:.0%} of the gap); switching to {nxt}",
                nxt,
            )
    return Decision("retry", f"{verdict}; retrying with notes", current_specialist)


def notes_from_validation(v: Validation | None, agent_notes: str = "", stopped: str = "") -> str:
    """What the next attempt should know about why this one failed."""
    if v is None:
        return f"The agent produced no patch (stopped: {stopped}). {agent_notes}".strip()[
            :NOTE_LIMIT
        ]
    lines = [f"Validation verdict: {v.verdict}."]
    for s in v.steps:
        if s.status == "failed":
            if s.name == "diff_policy":
                viol = s.detail.get("violations", [])
                lines.append(
                    "Policy violations: " + "; ".join(f"{x['rule']}: {x['detail']}" for x in viol)
                )
            elif s.name == "performance":
                bad = [c for c in s.detail.get("criteria", []) if not c["passed"]]
                lines.append(
                    "Acceptance not met: "
                    + "; ".join(
                        f"{c['label']} was {c['actual']:g}, needs {c['op']} {c['threshold']:g}"
                        for c in bad
                    )
                )
            else:
                out = s.detail.get("output") or s.detail.get("summary") or ""
                lines.append(f"Step {s.name} failed: {str(out)[-600:]}")
    return "\n".join(lines)[:NOTE_LIMIT]
