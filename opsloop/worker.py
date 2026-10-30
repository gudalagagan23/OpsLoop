"""OpsLoop worker: runs queued jobs (investigate, remediate, validate).

Without OPSLOOP_LLM_MODEL the worker stays in Stage 1 mode: incidents get the stub Report 1
and nothing is remediated. With a model configured it runs the real pipeline."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.agents.coding_agent import ApiToolLoopRunner
from opsloop.agents.investigator import generate_report1_stub, investigate
from opsloop.agents.profiles import SpecialistProfile, load_all, specialist_for
from opsloop.approvals import service as approvals
from opsloop.core import jobs, ledger
from opsloop.core.config import Settings, get_settings
from opsloop.core.db import get_engine
from opsloop.core.embedder import Embedder, get_embedder
from opsloop.core.llm import AnthropicLLM, LLMClient, LLMUsage
from opsloop.core.models import (
    Incident,
    IncidentEvent,
    LlmCall,
    RemediationAttempt,
    Report,
)
from opsloop.incidents.manager import IncidentManager
from opsloop.replay.scenario import Scenario
from opsloop.testloop.loop import validate
from opsloop.testloop.store import record_validation
from opsloop.workflows.remediation import (
    Deps,
    postgres_checkpointer,
    run_remediation,
    scenario_for_report,
)

log = logging.getLogger("opsloop.worker")

INVESTIGATE_SETTLE_S = 25.0  # let correlated signals attach before investigating


def make_recorder(sessions: sessionmaker[Session]) -> Callable[[LLMUsage], None]:
    """Audit every model call in its own transaction (survives a rolled-back job)."""

    def record(u: LLMUsage) -> None:
        with sessions() as s:
            s.add(
                LlmCall(
                    incident_id=u.incident_id,
                    agent=u.agent,
                    model=u.model,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cache_write_tokens=u.cache_write_tokens,
                    cost_usd=u.cost_usd,
                    latency_s=u.latency_s,
                    request_id=u.request_id,
                )
            )
            s.commit()

    return record


@dataclass
class Context:
    sessions: sessionmaker[Session]
    settings: Settings
    llm: LLMClient | None = None
    embedder_factory: Callable[[], Embedder] = get_embedder
    validator: Callable[..., Any] = validate
    profiles: dict[str, SpecialistProfile] = field(default_factory=load_all)
    checkpointer_factory: Callable[[], Any] | None = None
    _embedder: Embedder | None = None

    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = self.embedder_factory()
        return self._embedder


def make_llm(settings: Settings, recorder: Any) -> LLMClient:
    if settings.llm_provider == "groq":
        from opsloop.core.llm_groq import GroqLLM

        return GroqLLM(settings, recorder=recorder)
    if settings.llm_provider == "gemini":
        from opsloop.core.llm_gemini import GeminiLLM

        return GeminiLLM(settings, recorder=recorder)
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(settings, recorder=recorder)
    raise ValueError(f"unknown OPSLOOP_LLM_PROVIDER {settings.llm_provider!r}")


def build_context() -> Context:
    settings = get_settings()
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    llm = make_llm(settings, make_recorder(sessions)) if settings.llm_model else None
    if llm is None:
        log.warning("OPSLOOP_LLM_MODEL is not set: running in Stage 1 mode (stub Report 1 only)")
    return Context(sessions, settings, llm)


# ---- handlers ------------------------------------------------------------------------------


def handle_investigate(ctx: Context, session: Session, payload: dict[str, Any]) -> None:
    inc = session.get_one(Incident, payload["incident_id"])
    # INVESTIGATING is committed before the model call, so a retried job (say, after a provider
    # overload) finds it in that state and must resume, not skip.
    if inc.state not in ("TRIAGED", "INVESTIGATING"):
        return  # already investigated, dismissed or escalated: nothing to do
    mgr = IncidentManager(session)
    if ctx.llm is None:
        generate_report1_stub(session, mgr, inc)  # STUB path: no model configured
        return
    investigate(
        session,
        mgr,
        ctx.llm,
        ctx.embedder(),
        inc,
        escalation_confidence=ctx.settings.escalation_confidence,
    )
    session.flush()
    jobs.enqueue(
        session,
        "remediate",
        {"incident_id": inc.id, "run_id": "1"},
        idempotency_key=f"remediate:{inc.id}:1",
    )


def handle_remediate(ctx: Context, session: Session, payload: dict[str, Any]) -> None:
    if ctx.llm is None:
        raise RuntimeError("remediation needs OPSLOOP_LLM_MODEL")
    session.commit()  # release locks: the workflow opens its own sessions
    deps = Deps(
        sessions=ctx.sessions,
        runner=ApiToolLoopRunner(ctx.llm),
        settings=ctx.settings,
        validator=ctx.validator,
        profiles=ctx.profiles,
    )
    url = ctx.settings.database_url
    with (
        ctx.checkpointer_factory() if ctx.checkpointer_factory else postgres_checkpointer(url)
    ) as saver:
        out = run_remediation(
            deps,
            payload["incident_id"],
            saver,
            run_id=str(payload.get("run_id", "1")),
            notes=list(payload.get("notes", [])),
            exclude=list(payload.get("exclude", [])),
        )
    log.info("incident %s remediation outcome: %s", payload["incident_id"], out.get("outcome"))


def handle_validate(ctx: Context, session: Session, payload: dict[str, Any]) -> None:
    """Validate a human-written patch (an edit during approval, or a take-over fix)."""
    att = session.get_one(RemediationAttempt, payload["attempt_id"])
    if att.status != "pending_validation":
        return
    inc = session.get_one(Incident, att.incident_id)
    report = session.execute(
        select(Report)
        .where(Report.incident_id == inc.id, Report.kind == "report1")
        .order_by(Report.version.desc())
        .limit(1)
    ).scalar_one()
    scenario = scenario_for_report(session, report.body)
    profile = ctx.profiles.get(str(payload.get("profile"))) or approvals.profile_for_incident(
        session, inc.id
    )
    patch, attempt_id, incident_id = att.patch or "", att.id, inc.id
    session.commit()
    if scenario is None:
        validation = None
    else:
        validation = ctx.validator(patch, scenario, profile, strategy=att.strategy)
    with ctx.sessions() as s:
        att = s.get_one(RemediationAttempt, attempt_id)
        inc = s.get_one(Incident, incident_id)
        mgr = IncidentManager(s)
        record_validation(s, att, validation, patch=patch)
        if validation is not None and validation.passed:
            ev = ledger.append(
                s,
                type="action",
                source="orchestrator",
                incident_id=incident_id,
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
                    incident_id=incident_id,
                    ts=mgr.now(),
                    kind="approval_requested",
                    actor="orchestrator",
                    reason="validated fix awaits approval",
                    evidence_id=ev.id,
                )
            )
        else:
            why = (
                "no replay scenario"
                if scenario is None
                else (validation.verdict if validation else "?")
            )
            mgr.transition(
                inc,
                "ESCALATED",
                actor="orchestrator",
                reason=f"human patch did not validate: {why}",
            )
        s.commit()


def dispatch(ctx: Context) -> dict[str, jobs.Handler]:
    return {
        "investigate": lambda s, p: handle_investigate(ctx, s, p),
        "remediate": lambda s, p: handle_remediate(ctx, s, p),
        "validate": lambda s, p: handle_validate(ctx, s, p),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ctx = build_context()
    log.info("worker started (llm=%s)", ctx.settings.llm_model or "none")
    jobs.work_forever(ctx.sessions, dispatch(ctx))


if __name__ == "__main__":
    main()


__all__ = ["Context", "Scenario", "build_context", "dispatch", "main", "specialist_for"]
