import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.agents.learner import learn_from_resolved
from opsloop.approvals import service as svc
from opsloop.core import ledger
from opsloop.core.embedder import HashEmbedder
from opsloop.core.models import (
    Deployment,
    Incident,
    IncidentEvent,
    Job,
    KnowledgeChunk,
    Outcome,
    RemediationAttempt,
    Report,
    Signal,
)
from opsloop.deploy.health import Sample
from opsloop.deploy.release import ReleaseConfig, run_release
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.testloop.loop import version_hash
from opsloop.testloop.patchutil import make_diff

pytestmark = pytest.mark.integration

PATCH = make_diff("taskflow/api/main.py", "leak = True\n", "leak = False\n")
BASE = "base+abc"


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeRouter:
    """Synthetic traffic following the split: the candidate behaves as `cand`, stable as `stable`."""

    def __init__(
        self,
        clock: Clock,
        cand: Callable[[], tuple[int, float]],
        stable: Callable[[], tuple[int, float]],
    ) -> None:
        self.clock, self.cand, self.stable = clock, cand, stable
        self.pct = 0
        self.splits: list[int] = []

    def set_split(self, pct: int) -> None:
        self.pct = pct
        self.splits.append(pct)

    def samples(self, since: float) -> list[Sample]:
        out = []
        for i in range(int((self.clock.t - since) * 20)):
            to_cand = (i % 100) < self.pct
            status, ms = (self.cand if to_cand else self.stable)()
            out.append(
                Sample(since + i / 20, "candidate" if to_cand else "stable", status, ms, "list")
            )
        return out


class FakeBackend:
    def __init__(self) -> None:
        self.started = self.stopped = False

    def start_candidate(self, patch: str, version_hash: str, flags: list[str]) -> str:
        self.started = True
        return "cand"

    def candidate_healthy(self) -> bool:
        return self.started

    def stable_healthy(self) -> bool:
        return True

    def stop_candidate(self) -> None:
        self.stopped = True


class FakeRig:
    def __init__(self, router: FakeRouter, on_promote: Callable[[], None] = lambda: None) -> None:
        self.router = router
        self.be = FakeBackend()
        self.promoted: list[str] = []
        self.stopped = False
        self.on_promote = on_promote

    def start(self) -> None:
        pass

    def backend(self, flags: list[str]) -> FakeBackend:
        return self.be

    def warmup(self, seconds: float) -> list[Sample]:
        return []

    def promote(self, fault: str) -> None:
        self.promoted.append(fault)
        self.on_promote()

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False)


def _approved(sessions: sessionmaker[Session], *, ttl_s: float = 600) -> tuple[int, str, int]:
    """An incident with a validated fault-4 fix, approved for its exact version."""
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    vh = version_hash(BASE, PATCH)
    with sessions() as s:
        mgr = IncidentManager(s, run_tag=tag)
        inc = mgr.ingest(
            FiredSignal(
                watcher="database",
                signal_id="db.pool_saturation",
                series_key=tag,
                labels={},
                service=f"svc-{tag}",
                severity="S1",
                method="threshold",
                query="q",
                value=1.0,
                baseline=0.5,
                condition_since=now - timedelta(seconds=30),
                fired_at=now,
                title="Requests timing out waiting for a DB connection",
            )
        )
        for st in ("INVESTIGATING", "REPORT1_READY", "FIXING", "VALIDATING", "AWAITING_APPROVAL"):
            mgr.transition(inc, st, actor="test")
        ev = ledger.append(s, type="hypothesis", source="test", incident_id=inc.id, payload={})
        s.add(
            Report(
                incident_id=inc.id,
                kind="report1",
                version=1,
                evidence_id=ev.id,
                body={
                    "summary": "pool exhausted",
                    "suspected_root_cause": {
                        "description": "leaked connections",
                        "category": "database",
                        "confidence": 0.9,
                    },
                },
            )
        )
        s.add(
            Report(
                incident_id=inc.id,
                kind="report2",
                version=1,
                evidence_id=ev.id,
                body={
                    "verdict": "passed",
                    "scenario": "f4_pool_exhausted",
                    "strategy": "code_fix",
                    "diff": PATCH,
                    "base_commit": BASE,
                    "version_hash": vh,
                },
            )
        )
        s.add(
            RemediationAttempt(
                incident_id=inc.id,
                attempt_no=1,
                strategy="code_fix",
                specialist="database_specialist",
                status="passed",
                patch=PATCH,
                version_hash=vh,
            )
        )
        s.flush()
        svc.approve(s, inc.id, vh, "alice", ttl_s=ttl_s)
        sig = s.execute(select(Signal.id).where(Signal.incident_id == inc.id)).scalar_one()
        s.commit()
        return inc.id, vh, sig


def _clear(sessions: sessionmaker[Session], signal_id: int) -> Callable[[], None]:
    def clear() -> None:
        with sessions() as s:
            s.get_one(Signal, signal_id).cleared_at = datetime.now(UTC)
            s.commit()

    return clear


CFG = ReleaseConfig(soak_s=30, warmup_s=0, verify_s=60, poll_s=2)


def test_approval_queues_a_release_for_that_exact_version(sessions: sessionmaker[Session]) -> None:
    inc_id, vh, _ = _approved(sessions)
    with sessions() as s:
        job = s.execute(
            select(Job).where(Job.idempotency_key == f"release:{inc_id}:{vh[:16]}")
        ).scalar_one()
        assert job.queue == "release" and job.payload == {"incident_id": inc_id, "version_hash": vh}


def test_good_fix_rolls_out_5_25_100_and_the_judge_resolves_it(
    sessions: sessionmaker[Session],
) -> None:
    inc_id, vh, sig = _approved(sessions)
    clock = Clock()
    router = FakeRouter(clock, cand=lambda: (200, 30.0), stable=lambda: (500, 3000.0))
    rig = FakeRig(router, on_promote=_clear(sessions, sig))
    verdict = run_release(sessions, inc_id, vh, lambda: rig, CFG, sleep=clock.sleep, clock=clock)
    assert verdict == "RESOLVED"
    assert router.splits[:3] == [5, 25, 100] and rig.promoted == ["4"] and rig.stopped
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "RESOLVED"
        dep = s.execute(select(Deployment).where(Deployment.incident_id == inc_id)).scalar_one()
        assert dep.state == "completed" and dep.stage_pct == 100 and dep.version_hash == vh
        events = [e["event"] for e in dep.stage_log["events"]]
        assert events[:2] == ["candidate_ready", "stage_started"] and events[-1] == "promoted"
        out = s.execute(select(Outcome).where(Outcome.incident_id == inc_id)).scalar_one()
        assert out.verdict == "RESOLVED" and out.deployment_id == dep.id
        progress = (
            s.execute(
                select(IncidentEvent.reason).where(
                    IncidentEvent.incident_id == inc_id, IncidentEvent.kind == "release_progress"
                )
            )
            .scalars()
            .all()
        )
        assert any("25% of traffic" in p for p in progress)
        assert ledger.verify(s).ok


def test_bad_fix_is_rolled_back_automatically_and_judged_unsuccessful(
    sessions: sessionmaker[Session],
) -> None:
    inc_id, vh, _ = _approved(sessions)
    clock = Clock()
    router = FakeRouter(clock, cand=lambda: (500, 30.0), stable=lambda: (200, 30.0))
    rig = FakeRig(router)
    verdict = run_release(sessions, inc_id, vh, lambda: rig, CFG, sleep=clock.sleep, clock=clock)
    assert verdict == "UNSUCCESSFUL"
    assert rig.promoted == [] and rig.be.stopped and router.pct == 0
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "UNSUCCESSFUL"
        dep = s.execute(select(Deployment).where(Deployment.incident_id == inc_id)).scalar_one()
        assert dep.state == "rolled_back" and dep.stage_pct == 5
        states = (
            s.execute(
                select(IncidentEvent.to_state).where(
                    IncidentEvent.incident_id == inc_id, IncidentEvent.kind == "state_change"
                )
            )
            .scalars()
            .all()
        )
        assert "ROLLED_BACK" in states


def test_expired_approval_sends_the_incident_back_for_review(
    sessions: sessionmaker[Session],
) -> None:
    inc_id, vh, _ = _approved(sessions, ttl_s=-1)
    rig = FakeRig(FakeRouter(Clock(), lambda: (200, 1.0), lambda: (200, 1.0)))
    assert run_release(sessions, inc_id, vh, lambda: rig, CFG) is None
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "AWAITING_APPROVAL"
    assert rig.router.splits == []


def test_patch_that_no_longer_matches_its_hash_is_not_deployed(
    sessions: sessionmaker[Session],
) -> None:
    inc_id, vh, _ = _approved(sessions)
    with sessions() as s:
        att = s.execute(
            select(RemediationAttempt).where(RemediationAttempt.incident_id == inc_id)
        ).scalar_one()
        att.patch = PATCH.replace("False", "None")
        s.commit()
    rig = FakeRig(FakeRouter(Clock(), lambda: (200, 1.0), lambda: (200, 1.0)))
    assert run_release(sessions, inc_id, vh, lambda: rig, CFG) is None
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "AWAITING_APPROVAL"


def test_release_runs_once(sessions: sessionmaker[Session]) -> None:
    inc_id, vh, sig = _approved(sessions)
    clock = Clock()
    router = FakeRouter(clock, cand=lambda: (200, 30.0), stable=lambda: (200, 30.0))
    rig = FakeRig(router, on_promote=_clear(sessions, sig))
    run_release(sessions, inc_id, vh, lambda: rig, CFG, sleep=clock.sleep, clock=clock)
    assert run_release(sessions, inc_id, vh, lambda: rig, CFG) is None  # already released


def test_learner_adds_only_resolved_fixes_to_the_knowledge_base(
    sessions: sessionmaker[Session],
) -> None:
    inc_id, vh, sig = _approved(sessions)
    clock = Clock()
    router = FakeRouter(clock, cand=lambda: (200, 30.0), stable=lambda: (500, 3000.0))
    rig = FakeRig(router, on_promote=_clear(sessions, sig))
    run_release(sessions, inc_id, vh, lambda: rig, CFG, sleep=clock.sleep, clock=clock)
    with sessions() as s:
        chunk_id = learn_from_resolved(s, HashEmbedder(), inc_id)
        s.commit()
        assert chunk_id is not None
        row = s.get_one(KnowledgeChunk, chunk_id)
        assert row.kind == "past_incident" and "leaked connections" in row.text
        assert learn_from_resolved(s, HashEmbedder(), inc_id) is None  # learned once

    bad_id, bad_vh, _ = _approved(sessions)
    bad = FakeRig(FakeRouter(Clock(), lambda: (500, 30.0), lambda: (200, 30.0)))
    c2 = Clock()
    bad.router.clock = c2
    run_release(sessions, bad_id, bad_vh, lambda: bad, CFG, sleep=c2.sleep, clock=c2)
    with sessions() as s:
        assert learn_from_resolved(s, HashEmbedder(), bad_id) is None


def test_bad_release_drill_is_approved_by_its_starter_and_rolled_back(
    sessions: sessionmaker[Session],
) -> None:
    from opsloop.deploy.release import start_bad_release_drill

    with sessions() as s:
        inc_id = start_bad_release_drill(s, "alice", PATCH)
        s.commit()
        approval = svc.require_valid_approval(s, inc_id, version_hash("drill", PATCH))
        assert approval.approver == "alice"
        job = s.execute(select(Job).where(Job.queue == "release")).scalars().all()
        assert any(j.payload["incident_id"] == inc_id for j in job)
    clock = Clock()
    rig = FakeRig(FakeRouter(clock, cand=lambda: (500, 30.0), stable=lambda: (200, 30.0)))
    verdict = run_release(
        sessions,
        inc_id,
        version_hash("drill", PATCH),
        lambda: rig,
        CFG,
        sleep=clock.sleep,
        clock=clock,
    )
    assert verdict == "UNSUCCESSFUL" and rig.promoted == []
