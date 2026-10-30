"""Remediation workflow (LangGraph): plan -> attempt -> decide -> (retry | switch | submit |
escalate). State is checkpointed in PostgreSQL after every node, so a crashed worker resumes
where it stopped. Nodes are idempotent: re-running one never duplicates an attempt row.

Budgets and routing come from opsloop.agents.orchestrator; validation is the plain-code Test
Loop. Nothing here applies a change outside a sandbox."""

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.agents.coding_agent import AgentResult, Brief, Workspace
from opsloop.agents.orchestrator import (
    Budgets,
    choose_strategy,
    decide,
    notes_from_validation,
)
from opsloop.agents.profiles import SpecialistProfile, load_all, specialist_for
from opsloop.core import ledger
from opsloop.core.config import Settings, get_settings
from opsloop.core.models import (
    Incident,
    IncidentEvent,
    KnowledgeChunk,
    LlmCall,
    RemediationAttempt,
    Report,
)
from opsloop.incidents.manager import IncidentManager
from opsloop.replay.scenario import Scenario
from opsloop.sandbox.runner import Sandbox, export_tree
from opsloop.testloop.loop import Validation, validate
from opsloop.testloop.store import record_validation

log = logging.getLogger("opsloop.remediation")


class Runner(Protocol):
    def run(
        self,
        brief: Brief,
        ws: Workspace,
        profile: SpecialistProfile,
        *,
        cost_budget_usd: float = ...,
        time_budget_s: float = ...,
    ) -> AgentResult: ...


WorkspaceFactory = Callable[[Scenario, str | None], AbstractContextManager[Workspace]]
Validator = Callable[..., Validation]


@contextlib.contextmanager
def sandbox_workspace(scenario: Scenario, base_ref: str | None) -> Iterator[Workspace]:
    """The agent's authoring sandbox: clean export of the deployed code, no hidden tests."""
    with Sandbox(flags=scenario.flags) as sb:
        sb.load_tree(export_tree(base_ref))
        yield sb


@dataclass
class Deps:
    sessions: sessionmaker[Session]
    runner: Runner
    settings: Settings = field(default_factory=get_settings)
    workspace: WorkspaceFactory = sandbox_workspace
    validator: Validator = validate
    profiles: dict[str, SpecialistProfile] = field(default_factory=load_all)
    base_ref: str | None = None
    clock: Callable[[], float] = time.time

    @property
    def budgets(self) -> Budgets:
        s = self.settings
        return Budgets(
            s.max_attempts,
            s.max_specialist_switches,
            s.incident_time_budget_s,
            s.incident_cost_cap_usd,
        )


class State(TypedDict, total=False):
    incident_id: int
    strategy: str
    fault: str
    specialist: str
    category_specialist: str
    tried: list[str]
    attempt_no: int
    switches: int
    notes: list[str]
    cost_usd: float
    started_at: float
    verdict: str
    gap_closed: float
    action: str
    reason: str
    outcome: str  # awaiting_approval | escalated
    exclude: list[str]  # strategies a human rejected


def scenario_for_report(session: Session, body: dict[str, Any]) -> Scenario | None:
    """Replay scenario for an incident: the known incident the Investigator matched."""
    ids: list[int] = list(body.get("sources", []))
    rows = {
        r.id: r
        for r in session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.id.in_(ids), KnowledgeChunk.kind == "known_incident"
            )
        ).scalars()
    }
    for i in ids:
        row = rows.get(i)
        if row is not None:
            try:
                return Scenario.by_fault(str(row.meta.get("fault")))
            except KeyError:
                continue
    return None


def _latest_report(session: Session, incident_id: int, kind: str = "report1") -> Report:
    return session.execute(
        select(Report)
        .where(Report.incident_id == incident_id, Report.kind == kind)
        .order_by(Report.version.desc())
        .limit(1)
    ).scalar_one()


def _llm_cost(session: Session, incident_id: int) -> float:
    return float(
        session.execute(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
                LlmCall.incident_id == incident_id
            )
        ).scalar_one()
    )


def build_brief(
    report: dict[str, Any], scenario: Scenario, notes: list[str], incident: Incident
) -> Brief:
    rc = report["suspected_root_cause"]
    return Brief(
        incident_id=incident.id,
        title=incident.title,
        symptoms=report["summary"],
        root_cause=rc["description"],
        category=rc["category"],
        evidence_summary=(
            f"{len(report['evidence_for'])} ledger entries support this; "
            f"uncertainty: {report['uncertainty']}"
        ),
        acceptance=[f"{c['label']}: {c['op']} {c['value']}" for c in scenario.acceptance],
        previous_notes=list(notes),
    )


def build_graph(deps: Deps) -> StateGraph:  # type: ignore[type-arg]
    sessions = deps.sessions

    def escalate_state(session: Session, mgr: IncidentManager, inc: Incident, reason: str) -> None:
        if inc.state not in ("ESCALATED",):
            mgr.transition(inc, "ESCALATED", actor="orchestrator", reason=reason)
        ledger.append(
            session,
            type="action",
            source="orchestrator",
            incident_id=inc.id,
            payload={"action": "escalate", "reason": reason},
        )

    # ---- plan --------------------------------------------------------------------------
    def plan(state: State) -> State:
        with sessions() as s:
            inc = s.get_one(Incident, state["incident_id"])
            mgr = IncidentManager(s)
            report = _latest_report(s, inc.id)
            body = report.body
            strategy, why = choose_strategy(
                body, deps.settings.escalation_confidence, tuple(state.get("exclude", []))
            )
            if strategy == "rollback":
                # Automated rollback needs the deployment controller, which this workflow does not
                # drive yet: take the Investigator's next recommendation instead of giving up.
                fallback, fb_why = choose_strategy(
                    body,
                    deps.settings.escalation_confidence,
                    (*state.get("exclude", []), "rollback"),
                )
                if fallback != "escalate":
                    why = f"rollback is not automated yet; next recommendation: {fb_why}"
                    strategy = fallback
            scenario = scenario_for_report(s, body)
            specialist = specialist_for(body["suspected_root_cause"]["category"], deps.profiles)
            ledger.append(
                s,
                type="strategy",
                source="orchestrator",
                incident_id=inc.id,
                payload={
                    "strategy": strategy,
                    "reason": why,
                    "specialist": specialist,
                    "scenario": scenario.id if scenario else None,
                },
            )
            out: State = {
                "strategy": strategy,
                "specialist": specialist,
                "category_specialist": specialist,
                "tried": [specialist],
                "attempt_no": 0,
                "switches": 0,
                "notes": list(state.get("notes", [])),
                "cost_usd": 0.0,
                "started_at": deps.clock(),
            }
            if strategy == "rollback":
                # STUB (Stage 3): rollback proposals need the deployment controller.
                out |= {
                    "outcome": "escalated",
                    "reason": "rollback needs the deployment controller",
                }
            elif strategy == "escalate":
                out |= {"outcome": "escalated", "reason": why}
            elif scenario is None:
                out |= {
                    "outcome": "escalated",
                    "reason": "no replay scenario matches this incident (no known incident retrieved)",
                }
            else:
                out["fault"] = scenario.fault
            if out.get("outcome") == "escalated":
                escalate_state(s, mgr, inc, str(out["reason"]))
            s.commit()
            return out

    # ---- attempt -----------------------------------------------------------------------
    def attempt(state: State) -> State:
        attempt_no = state["attempt_no"] + 1
        specialist = state["specialist"]
        profile = deps.profiles[specialist]
        scenario = Scenario.by_fault(state["fault"])
        with sessions() as s:
            inc = s.get_one(Incident, state["incident_id"])
            mgr = IncidentManager(s)
            if inc.state != "FIXING":
                mgr.transition(
                    inc,
                    "FIXING",
                    actor="orchestrator",
                    reason=f"attempt {attempt_no} with {specialist}",
                )
            row = s.execute(
                select(RemediationAttempt).where(
                    RemediationAttempt.incident_id == inc.id,
                    RemediationAttempt.attempt_no == attempt_no,
                )
            ).scalar_one_or_none()
            if row is None:
                row = RemediationAttempt(
                    incident_id=inc.id,
                    attempt_no=attempt_no,
                    strategy=state["strategy"],
                    specialist=specialist,
                    status="running",
                )
                s.add(row)
                ledger.append(
                    s,
                    type="action",
                    source="orchestrator",
                    incident_id=inc.id,
                    payload={
                        "action": "attempt_started",
                        "attempt": attempt_no,
                        "specialist": specialist,
                        "notes": state["notes"][-1:],
                    },
                )
            s.flush()
            attempt_id = row.id
            brief = build_brief(_latest_report(s, inc.id).body, scenario, state["notes"], inc)
            spent = max(state["cost_usd"], _llm_cost(s, inc.id))
            s.commit()

        remaining_cost = max(0.0, deps.budgets.cost_usd - spent)
        remaining_time = max(30.0, deps.budgets.time_s - (deps.clock() - state["started_at"]))
        with deps.workspace(scenario, deps.base_ref) as ws:
            result = deps.runner.run(
                brief, ws, profile, cost_budget_usd=remaining_cost, time_budget_s=remaining_time
            )
        patch = result.patch.strip("\n") + "\n" if result.patch.strip() else ""

        validation: Validation | None = None
        if patch:
            with sessions() as s:
                inc = s.get_one(Incident, state["incident_id"])
                mgr = IncidentManager(s)
                ledger.append(
                    s,
                    type="patch",
                    source=f"agent:{specialist}",
                    incident_id=inc.id,
                    payload={
                        "attempt": attempt_no,
                        "patch": patch,
                        "agent_notes": result.notes,
                        "agent_stopped": result.stopped,
                        "agent_turns": result.turns,
                    },
                )
                mgr.transition(
                    inc,
                    "VALIDATING",
                    actor="orchestrator",
                    reason=f"validating attempt {attempt_no}",
                )
                s.commit()
            validation = deps.validator(
                patch, scenario, profile, strategy=state["strategy"], base_ref=deps.base_ref
            )

        verdict = validation.verdict if validation else "agent_failed"
        gap = validation.gap_closed if validation else 0.0
        with sessions() as s:
            att = s.get_one(RemediationAttempt, attempt_id)
            record_validation(
                s, att, validation, patch=patch, agent_notes=result.notes, cost_usd=result.cost_usd
            )
            s.commit()
        return {
            "attempt_no": attempt_no,
            "verdict": verdict,
            "gap_closed": gap,
            "cost_usd": state["cost_usd"] + result.cost_usd,
            "notes": [
                *state["notes"],
                notes_from_validation(validation, result.notes, result.stopped),
            ]
            if verdict != "passed"
            else state["notes"],
        }

    # ---- decide ------------------------------------------------------------------------
    def decide_node(state: State) -> State:
        with sessions() as s:
            cost = max(state["cost_usd"], _llm_cost(s, state["incident_id"]))
        d = decide(
            verdict=state["verdict"],
            gap_closed=state["gap_closed"],
            attempt_no=state["attempt_no"],
            switches=state["switches"],
            elapsed_s=deps.clock() - state["started_at"],
            cost_usd=cost,
            budgets=deps.budgets,
            current_specialist=state["specialist"],
            category_specialist=state["category_specialist"],
            tried=state["tried"],
            profiles=deps.profiles,
        )
        out: State = {"action": d.action, "reason": d.reason, "cost_usd": cost}
        if d.action == "switch" and d.specialist:
            out |= {
                "specialist": d.specialist,
                "switches": state["switches"] + 1,
                "tried": [*state["tried"], d.specialist],
            }
        with sessions() as s:
            ledger.append(
                s,
                type="action",
                source="orchestrator",
                incident_id=state["incident_id"],
                payload={
                    "action": "decision",
                    "decision": d.action,
                    "reason": d.reason,
                    "attempt": state["attempt_no"],
                    "verdict": state["verdict"],
                },
            )
            s.commit()
        return out

    # ---- terminal nodes ------------------------------------------------------------------
    def submit(state: State) -> State:
        with sessions() as s:
            inc = s.get_one(Incident, state["incident_id"])
            mgr = IncidentManager(s)
            att = s.execute(
                select(RemediationAttempt)
                .where(
                    RemediationAttempt.incident_id == inc.id, RemediationAttempt.status == "passed"
                )
                .order_by(RemediationAttempt.attempt_no.desc())
                .limit(1)
            ).scalar_one()
            ev = ledger.append(
                s,
                type="action",
                source="orchestrator",
                incident_id=inc.id,
                payload={
                    "action": "approval_requested",
                    "version_hash": att.version_hash,
                    "attempt": att.attempt_no,
                },
            )
            mgr.transition(
                inc,
                "AWAITING_APPROVAL",
                actor="orchestrator",
                reason=f"validated version {str(att.version_hash)[:12]}",
            )
            s.add(
                IncidentEvent(
                    incident_id=inc.id,
                    ts=mgr.now(),
                    kind="approval_requested",
                    actor="orchestrator",
                    reason="validated fix awaits approval",
                    evidence_id=ev.id,
                )
            )
            s.commit()
        return {"outcome": "awaiting_approval"}

    def escalate(state: State) -> State:
        with sessions() as s:
            inc = s.get_one(Incident, state["incident_id"])
            if inc.state != "ESCALATED":
                escalate_state(s, IncidentManager(s), inc, state.get("reason", "escalated"))
            s.commit()
        return {"outcome": "escalated"}

    # ---- wiring --------------------------------------------------------------------------
    g: StateGraph = StateGraph(State)  # type: ignore[type-arg]
    g.add_node("plan", plan)
    g.add_node("attempt", attempt)
    g.add_node("decide", decide_node)
    g.add_node("submit", submit)
    g.add_node("escalate", escalate)
    g.add_edge(START, "plan")
    g.add_conditional_edges(
        "plan",
        lambda s: "escalated" if s.get("outcome") == "escalated" else "go",
        {"escalated": END, "go": "attempt"},
    )
    g.add_edge("attempt", "decide")
    g.add_conditional_edges(
        "decide",
        lambda s: s["action"],
        {"accept": "submit", "escalate": "escalate", "retry": "attempt", "switch": "attempt"},
    )
    g.add_edge("submit", END)
    g.add_edge("escalate", END)
    return g


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextlib.contextmanager
def postgres_checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(_libpq(database_url)) as saver:
        saver.setup()
        yield saver


def run_remediation(
    deps: Deps,
    incident_id: int,
    checkpointer: Any,
    *,
    run_id: str = "1",
    notes: list[str] | None = None,
    exclude: list[str] | None = None,
) -> State:
    """Run (or resume) the remediation workflow for one incident.

    A rejection starts a new run (new run_id) carrying the human's notes and the strategies
    they ruled out; an interrupted run with the same run_id resumes from its checkpoint."""
    graph = build_graph(deps).compile(checkpointer=checkpointer)
    cfg: RunnableConfig = {"configurable": {"thread_id": f"remediation-{incident_id}-{run_id}"}}
    snap = graph.get_state(cfg)
    if snap.next:  # an earlier run stopped part-way: continue it
        log.info("resuming remediation of incident %s at %s", incident_id, snap.next)
        return cast(State, graph.invoke(None, cfg))
    if snap.values.get("outcome"):
        return cast(State, snap.values)
    start: State = {"incident_id": incident_id, "notes": notes or [], "exclude": exclude or []}
    return cast(State, graph.invoke(start, cfg))
