import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.agents.coding_agent import AgentResult
from opsloop.core import ledger
from opsloop.core.config import Settings
from opsloop.core.embedder import HashEmbedder
from opsloop.core.models import Incident, KnowledgeChunk, RemediationAttempt, Report
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.knowledge.ingest import ingest
from opsloop.testloop.loop import Step, Validation
from opsloop.workflows.remediation import Deps, postgres_checkpointer, run_remediation

pytestmark = pytest.mark.integration

PATCH = "diff --git a/taskflow/api/main.py b/taskflow/api/main.py\n--- a/taskflow/api/main.py\n+++ b/taskflow/api/main.py\n@@ -1 +1 @@\n-a\n+b\n"


class FakeRunner:
    def __init__(self, patches: list[str] | None = None, fail: bool = False) -> None:
        self.briefs: list[Any] = []
        self.profiles: list[str] = []
        self.patches = patches or [PATCH]
        self.fail = fail

    def run(self, brief: Any, ws: Any, profile: Any, **kw: Any) -> AgentResult:
        self.briefs.append(brief)
        self.profiles.append(profile.name)
        if self.fail:
            raise RuntimeError("worker crashed")
        patch = self.patches[min(len(self.briefs) - 1, len(self.patches) - 1)]
        return AgentResult(patch, "agent notes", 3, 4, 0.01, "finished")


@contextlib.contextmanager
def fake_workspace(scenario: Any, base_ref: Any) -> Iterator[object]:
    yield object()


def _validation(verdict: str, gap: float = 0.0, steps: list[Step] | None = None) -> Validation:
    h = uuid.uuid4().hex + uuid.uuid4().hex
    return Validation(
        verdict == "passed",
        verdict,
        h,
        "base+worktree",
        steps
        or [
            Step(
                "performance",
                "passed" if verdict == "passed" else "failed",
                1.0,
                {
                    "criteria": [
                        {
                            "passed": verdict == "passed",
                            "label": "queries",
                            "actual": 100,
                            "op": "<=",
                            "threshold": 5,
                        }
                    ]
                },
            )
        ],
        {"errors": 0},
        {"errors": 0},
        gap,
        {"verdict": verdict, "diff": PATCH, "version_hash": h},
    )


class FakeValidator:
    def __init__(self, *verdicts: tuple[str, float]) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0

    def __call__(self, patch: str, scenario: Any, profile: Any, **kw: Any) -> Validation:
        v, gap = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        return _validation(v, gap)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def kb_fault1(sessions: sessionmaker[Session]) -> int:
    with sessions() as s:
        s.execute(delete(KnowledgeChunk))
        ingest(s, HashEmbedder())
        s.commit()
        return next(
            c.id
            for c in s.execute(select(KnowledgeChunk)).scalars()
            if c.kind == "known_incident" and c.meta.get("fault") == "1"
        )


def _incident(
    sessions: sessionmaker[Session],
    *,
    confidence: float = 0.9,
    strategy: str = "code_fix",
    sources: list[int] | None = None,
    category: str = "api",
) -> int:
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    with sessions() as s:
        mgr = IncidentManager(s, run_tag=tag)
        inc = mgr.ingest(
            FiredSignal(
                watcher="api",
                signal_id="api.p95_latency",
                series_key=tag,
                labels={},
                service=f"svc-{tag}",
                severity="S2",
                method="threshold",
                query="q",
                value=1.0,
                baseline=0.3,
                condition_since=now - timedelta(seconds=30),
                fired_at=now,
                title="p95 high",
            )
        )
        mgr.transition(inc, "INVESTIGATING", actor="test")
        ev = ledger.append(
            s, type="hypothesis", source="test", incident_id=inc.id, payload={"r": 1}
        )
        body = {
            "summary": "list route slow",
            "evidence_for": [ev.id],
            "evidence_against": [],
            "uncertainty": "none",
            "sources": sources or [],
            "suspected_root_cause": {
                "description": "n+1",
                "category": category,
                "confidence": confidence,
            },
            "recommended_strategies": [{"strategy": strategy, "rationale": "r", "risk": "low"}],
            "deployment_correlation": {"deploy_event_id": None, "relation": "none"},
        }
        s.add(Report(incident_id=inc.id, kind="report1", version=1, body=body, evidence_id=ev.id))
        mgr.transition(inc, "REPORT1_READY", actor="test")
        s.commit()
        return inc.id


def _deps(
    sessions: sessionmaker[Session], runner: FakeRunner, validator: Any, **settings: Any
) -> Deps:
    return Deps(
        sessions=sessions,
        runner=runner,
        workspace=fake_workspace,
        validator=validator,
        settings=Settings(_env_file=None, **settings),
    )


def _state(sessions: sessionmaker[Session], inc_id: int) -> str:
    with sessions() as s:
        return s.get_one(Incident, inc_id).state


def test_validated_fix_waits_for_approval(sessions: sessionmaker[Session], kb_fault1: int) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    runner, validator = FakeRunner(), FakeValidator(("passed", 1.0))
    out = run_remediation(_deps(sessions, runner, validator), inc_id, InMemorySaver())
    assert out["outcome"] == "awaiting_approval"
    assert _state(sessions, inc_id) == "AWAITING_APPROVAL"
    with sessions() as s:
        att = s.execute(
            select(RemediationAttempt).where(RemediationAttempt.incident_id == inc_id)
        ).scalar_one()
        assert att.status == "passed" and att.version_hash and att.patch == PATCH
        assert s.execute(
            select(Report).where(Report.incident_id == inc_id, Report.kind == "report2")
        ).scalar_one()
        assert ledger.verify(s).ok
    assert runner.profiles == ["api_specialist"]


def test_failed_attempt_retries_with_notes_then_passes(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    runner = FakeRunner()
    out = run_remediation(
        _deps(sessions, runner, FakeValidator(("failed", 0.5), ("passed", 1.0))),
        inc_id,
        InMemorySaver(),
    )
    assert out["outcome"] == "awaiting_approval" and out["attempt_no"] == 2
    assert runner.briefs[0].previous_notes == []
    assert "queries was 100" in runner.briefs[1].previous_notes[0]
    assert runner.profiles == ["api_specialist", "api_specialist"]


def test_no_improvement_switches_to_the_generic_specialist(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    runner = FakeRunner()
    out = run_remediation(
        _deps(sessions, runner, FakeValidator(("failed", 0.0), ("passed", 1.0))),
        inc_id,
        InMemorySaver(),
    )
    assert runner.profiles == ["api_specialist", "generic_specialist"]
    assert out["switches"] == 1


def test_attempt_budget_escalates(sessions: sessionmaker[Session], kb_fault1: int) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    runner = FakeRunner()
    out = run_remediation(
        _deps(sessions, runner, FakeValidator(("failed", 0.5)), max_attempts=2),
        inc_id,
        InMemorySaver(),
    )
    assert out["outcome"] == "escalated" and len(runner.briefs) == 2
    assert _state(sessions, inc_id) == "ESCALATED"


def test_unreproducible_incident_escalates_after_one_attempt(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    runner = FakeRunner()
    out = run_remediation(
        _deps(sessions, runner, FakeValidator(("not_reproduced", 0.0))), inc_id, InMemorySaver()
    )
    assert out["outcome"] == "escalated" and len(runner.briefs) == 1
    assert "reproduced" in out["reason"]


def test_agent_that_returns_no_patch_is_retried_then_escalated(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    runner = FakeRunner(patches=[""])
    validator = FakeValidator(("passed", 1.0))
    out = run_remediation(
        _deps(sessions, runner, validator, max_attempts=2), inc_id, InMemorySaver()
    )
    assert out["outcome"] == "escalated" and validator.calls == 0


def test_low_confidence_and_missing_scenario_escalate_without_any_attempt(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    low = _incident(sessions, confidence=0.4, sources=[kb_fault1])
    none = _incident(sessions, sources=[])
    runner = FakeRunner()
    for inc_id, why in ((low, "below"), (none, "no replay scenario")):
        out = run_remediation(
            _deps(sessions, runner, FakeValidator(("passed", 1.0))), inc_id, InMemorySaver()
        )
        assert out["outcome"] == "escalated" and why in out["reason"]
        assert _state(sessions, inc_id) == "ESCALATED"
    assert runner.briefs == []


def test_crashed_run_resumes_from_the_postgres_checkpoint_without_duplicate_attempts(
    sessions: sessionmaker[Session], kb_fault1: int, db_engine: Engine
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    url = db_engine.url.render_as_string(hide_password=False)
    with postgres_checkpointer(url) as saver:
        crashing = _deps(sessions, FakeRunner(fail=True), FakeValidator(("passed", 1.0)))
        with pytest.raises(RuntimeError, match="worker crashed"):
            run_remediation(crashing, inc_id, saver)
        assert _state(sessions, inc_id) == "FIXING"
        healthy = FakeRunner()
        out = run_remediation(
            _deps(sessions, healthy, FakeValidator(("passed", 1.0))), inc_id, saver
        )
    assert out["outcome"] == "awaiting_approval"
    with sessions() as s:
        attempts = (
            s.execute(select(RemediationAttempt).where(RemediationAttempt.incident_id == inc_id))
            .scalars()
            .all()
        )
        assert len(attempts) == 1 and attempts[0].status == "passed"


def test_rollback_recommendation_falls_back_to_the_next_strategy(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    with sessions() as s:
        rep = s.execute(select(Report).where(Report.incident_id == inc_id)).scalar_one()
        body = dict(rep.body)
        body["recommended_strategies"] = [
            {"strategy": "rollback", "rationale": "recent deploy", "risk": "medium"},
            {"strategy": "code_fix", "rationale": "fix the handler", "risk": "low"},
        ]
        body["deployment_correlation"] = {"deploy_event_id": None, "relation": "likely"}
        rep.body = body
        s.commit()
    runner = FakeRunner()
    out = run_remediation(
        _deps(sessions, runner, FakeValidator(("passed", 1.0))), inc_id, InMemorySaver()
    )
    assert out["outcome"] == "awaiting_approval" and out["strategy"] == "code_fix"
    assert len(runner.briefs) == 1


def test_a_second_run_numbers_attempts_after_the_first_and_has_its_own_budget(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, sources=[kb_fault1])
    first = run_remediation(
        _deps(sessions, FakeRunner(), FakeValidator(("failed", 0.5)), max_attempts=2),
        inc_id,
        InMemorySaver(),
    )
    assert first["outcome"] == "escalated"
    second = run_remediation(  # a human sends the escalation back for another go
        _deps(sessions, FakeRunner(), FakeValidator(("passed", 1.0)), max_attempts=2),
        inc_id,
        InMemorySaver(),
        run_id="2",
    )
    assert second["outcome"] == "awaiting_approval" and second["attempt_no"] == 3
    with sessions() as s:
        rows = s.execute(
            select(RemediationAttempt.attempt_no, RemediationAttempt.status)
            .where(RemediationAttempt.incident_id == inc_id)
            .order_by(RemediationAttempt.attempt_no)
        ).all()
    assert [tuple(r) for r in rows] == [(1, "failed"), (2, "failed"), (3, "passed")]


def test_rollback_only_recommendation_still_escalates(
    sessions: sessionmaker[Session], kb_fault1: int
) -> None:
    inc_id = _incident(sessions, strategy="rollback", sources=[kb_fault1])
    out = run_remediation(
        _deps(sessions, FakeRunner(), FakeValidator(("passed", 1.0))), inc_id, InMemorySaver()
    )
    assert out["outcome"] == "escalated"
