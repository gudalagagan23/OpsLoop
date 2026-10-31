"""Release: an approved, validated fix goes through a staged rollout, then the Judge decides.

Approval enqueues a `release` job. The worker then:

1. checks the approval still stands for this exact version: the hash is recomputed from the
   stored patch, and an expired or superseded approval sends the incident back for review;
2. runs the patched TaskFlow as a candidate container and moves traffic to it through the
   router, 5% -> 25% -> 100%, rolling back on its own the moment candidate health falls below
   control (the rollout controller in rollout.py);
3. promotes it. SIMULATED: stable switches the planted fault off, which is exactly the code path
   the approved patch removes (fault 4 also returns its leaked connections, fault 3 rebuilds its
   index). Then it waits for the incident's signals to clear;
4. asks the Judge for a verdict on the measured requests and closes the incident with it. Only
   a RESOLVED fix is added to the knowledge base (the Learner's input).

Every step is written to the evidence ledger and the incident timeline, so the dashboard follows
the release live. The live pieces (router, traffic, candidate container, fault control) sit
behind the Rig interface, so the whole path is testable without containers."""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.agents.judge import Judgement, Target, judge
from opsloop.approvals.service import ApprovalError, require_valid_approval
from opsloop.core import ledger
from opsloop.core.models import (
    Deployment,
    Incident,
    Outcome,
    RemediationAttempt,
    Report,
    Signal,
)
from opsloop.deploy.health import Sample
from opsloop.deploy.rollout import Backend, Rollout, RolloutConfig, RolloutResult, Router
from opsloop.incidents.manager import IncidentManager
from opsloop.replay.scenario import Scenario
from opsloop.testloop.loop import version_hash as compute_hash

log = logging.getLogger("opsloop.release")

ACTOR = "deployment"
STAGES = (5, 25, 100)

# What each scenario's fix must achieve in production, and how to compare. A missing index is
# shared by stable and candidate (one database), so that one is judged before/after.
TARGETS: dict[str, tuple[Target, str]] = {
    "1": (Target("list_p95_ms", "<=", 300.0), "concurrent"),
    "2": (Target("create_error_rate", "<=", 0.005), "concurrent"),
    "3": (Target("list_p95_ms", "<=", 300.0), "before_after"),
    "4": (Target("error_rate", "<=", 0.02), "concurrent"),
}
DEFAULT_TARGET = (Target("p95_ms", "<=", 300.0), "concurrent")


@dataclass(frozen=True)
class ReleaseConfig:
    soak_s: float = 40.0  # per stage
    warmup_s: float = 10.0  # control-only traffic first: the before/after baseline
    verify_s: float = 150.0  # how long to wait for the incident's signals to clear
    poll_s: float = 2.0
    start_timeout_s: float = 150.0


class Rig(Protocol):
    """Live release machinery: router + traffic, the candidate backend, fault control."""

    router: Router

    def start(self) -> None: ...
    def backend(self, flags: list[str]) -> Backend: ...
    def warmup(self, seconds: float) -> list[Sample]: ...
    def promote(self, fault: str) -> None: ...
    def stop(self) -> None: ...


class ReleaseRefused(Exception):
    """The approval no longer covers what would be deployed."""


@dataclass
class Plan:
    incident_id: int
    deployment_id: int
    version_hash: str
    patch: str
    scenario: Scenario


def _now() -> datetime:
    return datetime.now(UTC)


def _finite(x: Any) -> Any:
    """JSON (and the ledger's canonical form) has no NaN: an undefined statistic becomes null."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_finite(v) for v in x]
    return x


def _latest(session: Session, model: Any, *where: Any) -> Any:
    q = select(model)
    for w in where:
        q = q.where(w)
    return session.execute(q.order_by(model.id.desc()).limit(1)).scalar_one_or_none()


# The bad-release drill (fault 2): a build that breaks task creation, shipped on purpose so the
# rollout's automatic rollback can be watched. Not a replay scenario, so it lives here.
DRILL = Scenario(
    id="drill_bad_release",
    fault="2",
    wing="api",
    ground_truth="a release that returns 500 on POST /tasks",
    expected_signals=[],
    category="api",
)


def _scenario(report2: dict[str, Any]) -> Scenario:
    sid = report2.get("scenario")
    if sid == DRILL.id:
        return DRILL
    for s in Scenario.load_all():
        if s.id == sid:
            return s
    raise ReleaseRefused(f"no replay scenario {sid!r} to release against")


def prepare(sessions: sessionmaker[Session], incident_id: int, version_hash: str) -> Plan | None:
    """Check the approval, then move the incident to DEPLOYING. None = nothing to do."""
    with sessions() as s:
        inc = s.get_one(Incident, incident_id)
        if inc.state != "APPROVED":
            return None  # already released, or the decision changed: idempotent no-op
        mgr = IncidentManager(s)
        try:
            try:
                require_valid_approval(s, incident_id, version_hash)
            except ApprovalError as exc:
                raise ReleaseRefused(str(exc)) from exc
            att = _latest(
                s,
                RemediationAttempt,
                RemediationAttempt.incident_id == incident_id,
                RemediationAttempt.version_hash == version_hash,
            )
            report2 = _latest(
                s, Report, Report.incident_id == incident_id, Report.kind == "report2"
            )
            if att is None or not att.patch or report2 is None:
                raise ReleaseRefused("no validated patch is stored for this version")
            if compute_hash(str(report2.body.get("base_commit", "")), att.patch) != version_hash:
                raise ReleaseRefused("the stored patch no longer matches the approved version hash")
            scenario = _scenario(report2.body)
        except ReleaseRefused as exc:
            mgr.transition(inc, "AWAITING_APPROVAL", actor=ACTOR, reason=f"release refused: {exc}")
            s.commit()
            log.warning("incident %s: release refused: %s", incident_id, exc)
            return None
        mgr.transition(
            inc,
            "DEPLOYING",
            actor=ACTOR,
            reason=f"staged rollout of {version_hash[:12]}: {' -> '.join(f'{p}%' for p in STAGES)}",
        )
        dep = Deployment(
            incident_id=incident_id,
            version_hash=version_hash,
            kind="patch",
            state="running",
            stage_pct=0,
            reasons={},
            stage_log={"events": []},
        )
        s.add(dep)
        s.flush()
        ledger.append(
            s,
            type="deploy_event",
            source=ACTOR,
            incident_id=incident_id,
            payload={
                "action": "release_started",
                "deployment_id": dep.id,
                "version_hash": version_hash,
                "stages": list(STAGES),
                "scenario": scenario.id,
            },
        )
        s.commit()
        return Plan(incident_id, dep.id, version_hash, att.patch, scenario)


_EVENT_TEXT = {
    "candidate_ready": "candidate is up and healthy",
    "stage_started": "{pct}% of traffic on the fix",
    "stage_passed": "{pct}% stage passed its health checks",
    "rolled_back": "rolled back automatically at {pct}%",
    "promoted": "promoted to 100% (simulated: the planted fault is switched off on stable)",
}


def record_event(
    sessions: sessionmaker[Session], plan: Plan, kind: str, pct: int, data: dict[str, Any]
) -> None:
    """One rollout step: deployment row, ledger, incident timeline (and so the live stream)."""
    clean = _finite(
        {k: v for k, v in data.items() if isinstance(v, str | int | float | bool | list)}
    )
    text = _EVENT_TEXT.get(kind, kind.replace("_", " ")).format(pct=pct)
    reasons = data.get("reasons")
    if kind == "rolled_back" and isinstance(reasons, list) and reasons:
        text += ": " + "; ".join(str(r) for r in reasons)
    with sessions() as s:
        dep = s.get_one(Deployment, plan.deployment_id)
        if kind == "stage_started":
            dep.stage_pct = pct
        dep.stage_log = {
            "events": [
                *dep.stage_log.get("events", []),
                {"t": time.time(), "event": kind, "pct": pct, **clean},
            ]
        }
        ev = ledger.append(
            s,
            type="deploy_event",
            source=ACTOR,
            incident_id=plan.incident_id,
            payload={"action": f"rollout_{kind}", "pct": pct, **clean},
        )
        IncidentManager(s).note(
            s.get_one(Incident, plan.incident_id),
            "release_progress",
            actor=ACTOR,
            reason=text,
            evidence_id=ev.id,
        )
        s.commit()


def _signals_cleared(sessions: sessionmaker[Session], incident_id: int) -> bool:
    with sessions() as s:
        open_ = s.execute(
            select(Signal.id).where(Signal.incident_id == incident_id, Signal.cleared_at.is_(None))
        ).first()
        return open_ is None


def wait_for_clear(
    sessions: sessionmaker[Session],
    incident_id: int,
    timeout_s: float,
    poll_s: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    deadline = clock() + timeout_s
    while True:
        if _signals_cleared(sessions, incident_id):
            return True
        if clock() >= deadline:
            return False
        sleep(poll_s)


def finish(
    sessions: sessionmaker[Session],
    plan: Plan,
    result: RolloutResult,
    verdict: Judgement,
) -> str:
    """Record the rollout outcome and the Judge's verdict; close the incident with it."""
    with sessions() as s:
        inc = s.get_one(Incident, plan.incident_id)
        mgr = IncidentManager(s)
        dep = s.get_one(Deployment, plan.deployment_id)
        dep.state = "completed" if result.outcome == "completed" else "rolled_back"
        dep.stage_pct = result.stage_pct
        dep.finished_at = _now()
        dep.reasons = _finite(
            {"reasons": result.reasons, "rollback_seconds": result.rollback_seconds}
        )
        summary = _finite(
            {
                "verdict": verdict.verdict,
                "target": verdict.target,
                "effects": verdict.effects,
                "stage_values": {str(k): v for k, v in verdict.stage_values.items()},
                "gap_closed": verdict.gap_closed,
                "candidate_samples": verdict.candidate_samples,
                "rollout": result.outcome,
                "stage_reached": result.stage_pct,
            }
        )
        ev = ledger.append(
            s,
            type="outcome",
            source="judge",
            incident_id=plan.incident_id,
            payload={**summary, "reasons": verdict.reasons, "mode": verdict.mode},
        )
        s.add(
            Outcome(
                incident_id=plan.incident_id,
                deployment_id=plan.deployment_id,
                verdict=verdict.verdict,
                mode=verdict.mode,
                summary=summary,
                explanation="; ".join(verdict.reasons),
                evidence_id=ev.id,
            )
        )
        why = f"Judge: {verdict.verdict.lower()}. " + "; ".join(verdict.reasons)
        if result.outcome == "rolled_back":
            mgr.transition(inc, "ROLLED_BACK", actor=ACTOR, reason="; ".join(result.reasons))
        if inc.state == "DEPLOYING":
            mgr.transition(inc, "VERIFYING", actor=ACTOR, reason="rollout finished")
        mgr.transition(inc, verdict.verdict, actor="judge", reason=why[:900])
        s.commit()
    return verdict.verdict


def run_release(
    sessions: sessionmaker[Session],
    incident_id: int,
    version_hash: str,
    rig_factory: Callable[[], Rig],
    cfg: ReleaseConfig | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    on_resolved: Callable[[int], None] | None = None,
) -> str | None:
    """Release one approved version end to end. Returns the verdict, or None if nothing ran."""
    cfg = cfg or ReleaseConfig()
    plan = prepare(sessions, incident_id, version_hash)
    if plan is None:
        return None
    target, mode = TARGETS.get(plan.scenario.fault, DEFAULT_TARGET)

    def on_event(kind: str, pct: int, data: dict[str, Any]) -> None:
        record_event(sessions, plan, kind, pct, data)

    rig = rig_factory()
    baseline: list[Sample] = []
    try:
        rig.start()
        baseline = rig.warmup(cfg.warmup_s)
        rollout = Rollout(
            rig.router,
            rig.backend(plan.scenario.flags),
            RolloutConfig(
                stages=STAGES,
                soak_s=cfg.soak_s,
                poll_s=cfg.poll_s,
                start_timeout_s=cfg.start_timeout_s,
            ),
            clock=clock,
            sleep=sleep,
            on_event=on_event,
        )
        result = rollout.run(plan.patch, plan.version_hash, plan.scenario.flags)
        if result.outcome == "completed":
            rig.promote(plan.scenario.fault)
            on_event("promoted", 100, {"fault": plan.scenario.fault})
    except Exception as exc:
        log.exception("incident %s: release crashed", incident_id)
        result = RolloutResult("rolled_back", 0, [f"release machinery failed: {exc}"], [])
    finally:
        rig.stop()

    cleared: bool | None = None
    if result.outcome == "completed":
        with sessions() as s:
            mgr = IncidentManager(s)
            mgr.transition(
                s.get_one(Incident, incident_id),
                "VERIFYING",
                actor=ACTOR,
                reason="rollout completed at 100%; waiting for the incident's signals to clear",
            )
            s.commit()
        cleared = wait_for_clear(sessions, incident_id, cfg.verify_s, cfg.poll_s, sleep, clock)
    verdict = judge(
        target=target,
        samples_by_stage={st.pct: st.samples for st in result.stages},
        rolled_back=result.outcome == "rolled_back",
        signals_cleared=cleared,
        mode=mode,
        baseline=baseline,
    )
    final = finish(sessions, plan, result, verdict)
    log.info("incident %s released: %s -> %s", incident_id, result.outcome, final)
    if final == "RESOLVED" and on_resolved is not None:
        on_resolved(incident_id)
    return final


def start_bad_release_drill(session: Session, approver: str, patch: str) -> int:
    """Open a drill incident whose 'validated' fix is a known-bad build, approved by `approver`
    (the person who started the drill), so the release job ships it and must roll it back."""
    from opsloop.approvals import service as approvals
    from opsloop.core.models import RemediationAttempt as Attempt
    from opsloop.core.models import Report as Rep

    now = _now()
    tag = f"drill-{int(now.timestamp())}"
    mgr = IncidentManager(session, run_tag=tag)
    from opsloop.incidents.manager import FiredSignal

    inc = mgr.ingest(
        FiredSignal(
            watcher="api",
            signal_id="drill.bad_release",
            series_key=tag,
            labels={"drill": "bad_release"},
            service="bad-release-drill",
            severity="S3",
            method="threshold",
            query="n/a",
            value=0,
            baseline=0,
            condition_since=now,
            fired_at=now,
            title="[drill] Ship a known-bad build: POST /tasks returns 500",
        )
    )
    for sig in session.execute(select(Signal).where(Signal.incident_id == inc.id)).scalars():
        sig.cleared_at = now  # nothing live fires for a drill
    for st in ("INVESTIGATING", "REPORT1_READY", "FIXING", "VALIDATING", "AWAITING_APPROVAL"):
        mgr.transition(inc, st, actor="drill", reason="bad-release drill")
    base = "drill"
    vh = compute_hash(base, patch)
    ev = ledger.append(
        session,
        type="action",
        source="drill",
        incident_id=inc.id,
        payload={"action": "bad_release_drill", "started_by": approver},
    )
    session.add(
        Rep(
            incident_id=inc.id,
            kind="report1",
            version=1,
            evidence_id=ev.id,
            body={
                "summary": "Drill: a build that breaks task creation is released on purpose.",
                "suspected_root_cause": {
                    "description": "known-bad build (drill)",
                    "category": "api",
                    "confidence": 1.0,
                },
                "evidence_for": [ev.id],
                "evidence_against": [],
                "affected_services": ["taskflow-api"],
                "uncertainty": "none: this is a drill",
                "recommended_strategies": [],
                "deployment_correlation": {"deploy_event_id": None, "relation": "none"},
            },
        )
    )
    session.add(
        Rep(
            incident_id=inc.id,
            kind="report2",
            version=1,
            evidence_id=ev.id,
            body={
                "verdict": "passed",
                "strategy": "drill",
                "scenario": DRILL.id,
                "diff": patch,
                "base_commit": base,
                "version_hash": vh,
                "tests": [],
                "performance": None,
                "security_findings": [],
                "remaining_risks": ["Known-bad on purpose: the rollout must roll it back."],
                "rollback_plan": "automatic: traffic returns to stable",
            },
        )
    )
    session.add(
        Attempt(
            incident_id=inc.id,
            attempt_no=1,
            strategy="drill",
            specialist="drill",
            status="passed",
            patch=patch,
            version_hash=vh,
        )
    )
    session.flush()
    approvals.approve(session, inc.id, vh, approver, ttl_s=900)
    return inc.id


# ---- the live rig ------------------------------------------------------------------------


class SplitRouter:
    """The rollout controller's Router, talking to the in-process router directly."""

    def __init__(self, split: Any) -> None:
        self.split = split

    def set_split(self, candidate_pct: int) -> None:
        self.split.set(candidate_pct)

    def samples(self, since: float) -> list[Sample]:
        return list(self.split.since(since))


class LiveRig:
    """Router on 127.0.0.1:8090 with synthetic user traffic through it, a candidate container
    on :8081, and TaskFlow's fault control for the (simulated) promotion."""

    def __init__(self, control_url: str, rps: float = 12.0, workers: int = 6) -> None:
        from opsloop.deploy.router import Split

        self.control_url = control_url
        self.rps, self.workers = rps, workers
        self.split = Split()
        self.router: Router = SplitRouter(self.split)
        self._server: Any = None
        self._traffic: Any = None
        self._backend: Any = None

    def start(self) -> None:
        from opsloop.deploy.gate import Traffic, start_router

        self._server = start_router(self.split)
        self._traffic = Traffic(self._token(), rps=self.rps, workers=self.workers)
        self._traffic.start()

    @staticmethod
    def _token() -> str:
        """A session for the synthetic user that drives traffic through the router."""
        from opsloop.deploy.gate import login

        for _ in range(2):
            try:
                return login()
            except Exception:  # noqa: BLE001 - stable is often the broken thing; see below
                time.sleep(2)
        # Stable cannot log anyone in (an exhausted pool is often the incident itself), so sign
        # the synthetic user's token the way TaskFlow does. Simulation load generator only.
        import os
        from datetime import timedelta

        import jwt
        from sqlalchemy import text

        from opsloop.core.db import get_engine

        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT u.id, u.tenant_id, t.plan FROM taskflow.users u "
                    "JOIN taskflow.tenants t ON t.id = u.tenant_id WHERE u.email = :e"
                ),
                {"e": "user1@tenant1.sim"},
            ).one()
        secret = os.environ.get("TASKFLOW_JWT_SECRET", "sim-dev-secret-not-for-production")
        claims = {
            "sub": str(row.id),
            "tid": row.tenant_id,
            "plan": row.plan,
            "exp": _now() + timedelta(hours=1),
        }
        return str(jwt.encode(claims, secret, algorithm="HS256"))

    def backend(self, flags: list[str]) -> Backend:
        from opsloop.deploy.candidate import DockerCandidateBackend

        self._backend = DockerCandidateBackend(flags=flags)
        return self._backend  # type: ignore[no-any-return]

    def warmup(self, seconds: float) -> list[Sample]:
        t0 = time.time()
        time.sleep(seconds)
        return list(self.split.since(t0))

    def promote(self, fault: str) -> None:
        import httpx

        if fault == DRILL.fault:
            return  # the drill's build is never promoted; nothing to switch off
        httpx.post(f"{self.control_url}/faults/{fault}/off", timeout=15).raise_for_status()

    def stop(self) -> None:
        self.split.set(0)
        if self._backend is not None:
            self._backend.stop_candidate()
        if self._traffic is not None:
            self._traffic.halt()
        if self._server is not None:
            self._server.should_exit = True
            time.sleep(1.5)  # let the router release its port before the next release


__all__ = ["TARGETS", "LiveRig", "Plan", "ReleaseConfig", "Rig", "run_release"]
