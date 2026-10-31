import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.approvals import service as svc
from opsloop.core.config import Settings
from opsloop.core.embedder import HashEmbedder
from opsloop.core.llm import ScriptedLLM
from opsloop.core.models import Incident, Job, KnowledgeChunk, RemediationAttempt, Report
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.knowledge.ingest import ingest
from opsloop.testloop.loop import Step, Validation
from opsloop.testloop.patchutil import make_diff
from opsloop.worker import Context, dispatch, handle_investigate, handle_validate

pytestmark = pytest.mark.integration


def _triaged(sessions: sessionmaker[Session]) -> int:
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    with sessions() as s:
        inc = IncidentManager(s, run_tag=tag).ingest(
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
                baseline=0.9,
                condition_since=now - timedelta(seconds=30),
                fired_at=now,
                title="DB connection pool saturated",
            )
        )
        s.commit()
        return inc.id


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False)


def _ctx(sessions: sessionmaker[Session], llm: ScriptedLLM | None, **kw: Any) -> Context:
    return Context(sessions, Settings(_env_file=None), llm, embedder_factory=HashEmbedder, **kw)


def test_without_a_model_the_worker_produces_the_stub_report_only(
    sessions: sessionmaker[Session],
) -> None:
    inc_id = _triaged(sessions)
    ctx = _ctx(sessions, None)
    with sessions() as s:
        handle_investigate(ctx, s, {"incident_id": inc_id})
        s.commit()
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "REPORT1_READY"
        assert s.execute(select(Report).where(Report.incident_id == inc_id)).scalar_one().is_stub
        assert (
            s.execute(select(Job).where(Job.idempotency_key == f"remediate:{inc_id}:1")).first()
            is None
        )


def test_investigate_is_idempotent(sessions: sessionmaker[Session]) -> None:
    inc_id = _triaged(sessions)
    ctx = _ctx(sessions, None)
    for _ in range(2):
        with sessions() as s:
            handle_investigate(ctx, s, {"incident_id": inc_id})
            s.commit()
    with sessions() as s:
        assert (
            len(s.execute(select(Report).where(Report.incident_id == inc_id)).scalars().all()) == 1
        )


def test_a_retried_investigation_resumes_from_investigating(
    sessions: sessionmaker[Session],
) -> None:
    inc_id = _triaged(sessions)
    with sessions() as s:  # a first attempt got this far, then the model call failed
        IncidentManager(s).transition(s.get_one(Incident, inc_id), "INVESTIGATING", actor="t")
        s.commit()
    with sessions() as s:
        handle_investigate(_ctx(sessions, None), s, {"incident_id": inc_id})
        s.commit()
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "REPORT1_READY"


def test_with_a_model_investigation_queues_remediation(sessions: sessionmaker[Session]) -> None:
    with sessions() as s:
        s.execute(delete(KnowledgeChunk))
        ingest(s, HashEmbedder())
        s.commit()
    inc_id = _triaged(sessions)
    from opsloop.core.models import Evidence

    with sessions() as s:
        ev_id = (
            s.execute(select(Evidence.id).where(Evidence.incident_id == inc_id)).scalars().first()
        )
    llm = ScriptedLLM()
    llm.queue_structured(
        {
            "summary": "pool full",
            "evidence_for": [ev_id],
            "evidence_against": [],
            "affected_services": ["taskflow-api"],
            "uncertainty": "none found",
            "sources": [],
            "suspected_root_cause": {
                "description": "leak",
                "category": "database",
                "confidence": 0.9,
            },
            "deployment_correlation": {"deploy_event_id": None, "relation": "none"},
            "recommended_strategies": [{"strategy": "code_fix", "rationale": "r", "risk": "low"}],
        }
    )
    with sessions() as s:
        handle_investigate(_ctx(sessions, llm), s, {"incident_id": inc_id})
        s.commit()
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "REPORT1_READY"
        job = s.execute(
            select(Job).where(Job.idempotency_key == f"remediate:{inc_id}:1")
        ).scalar_one()
        assert job.queue == "remediate" and job.payload["incident_id"] == inc_id


def test_dispatch_has_the_four_queues(sessions: sessionmaker[Session]) -> None:
    queues = {"investigate", "remediate", "validate", "release"}
    assert set(dispatch(_ctx(sessions, None))) == queues


def _awaiting_with_human_patch(sessions: sessionmaker[Session]) -> tuple[int, int]:
    from opsloop.agents.profiles import load_profile

    inc_id = _triaged(sessions)
    with sessions() as s:
        mgr = IncidentManager(s)
        inc = s.get_one(Incident, inc_id)
        mgr.transition(inc, "INVESTIGATING", actor="t")
        ch = s.execute(select(KnowledgeChunk)).scalars().all()
        src = next(c.id for c in ch if c.kind == "known_incident" and c.meta.get("fault") == "4")
        s.add(Report(incident_id=inc_id, kind="report1", version=1, body={"sources": [src]}))
        for st in ("REPORT1_READY", "FIXING", "VALIDATING", "AWAITING_APPROVAL"):
            mgr.transition(inc, st, actor="t")
        s.add(
            RemediationAttempt(
                incident_id=inc_id,
                attempt_no=1,
                strategy="code_fix",
                specialist="database_specialist",
                status="passed",
                patch="x",
                version_hash="a" * 64,
            )
        )
        s.commit()
        patch = make_diff("taskflow/api/main.py", "a = 1\n", "a = 2\n")
        att = svc.submit_patch(
            s, inc_id, patch, "alice", load_profile("database_specialist"), kind="modify"
        )
        s.commit()
        return inc_id, att.id


def _validator(passed: bool) -> Any:
    def run(patch: str, scenario: Any, profile: Any, **kw: Any) -> Validation:
        h = uuid.uuid4().hex + uuid.uuid4().hex
        return Validation(
            passed,
            "passed" if passed else "failed",
            h,
            "b",
            [Step("performance", "passed" if passed else "failed")],
            {},
            {},
            1.0 if passed else 0.0,
            {"verdict": "passed" if passed else "failed"},
        )

    return run


def test_modified_patch_that_validates_returns_to_awaiting_approval(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as s:
        s.execute(delete(KnowledgeChunk))
        ingest(s, HashEmbedder())
        s.commit()
    inc_id, att_id = _awaiting_with_human_patch(sessions)
    handle_validate_ctx = _ctx(sessions, None, validator=_validator(True))
    with sessions() as s:
        handle_validate(
            handle_validate_ctx, s, {"attempt_id": att_id, "profile": "database_specialist"}
        )
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "AWAITING_APPROVAL"
        att = s.get_one(RemediationAttempt, att_id)
        assert att.status == "passed" and att.version_hash and att.version_hash != "a" * 64
        latest = svc.latest_passed_attempt(s, inc_id)
        assert latest is not None and latest.id == att_id


def test_modified_patch_that_fails_validation_escalates(sessions: sessionmaker[Session]) -> None:
    with sessions() as s:
        s.execute(delete(KnowledgeChunk))
        ingest(s, HashEmbedder())
        s.commit()
    inc_id, att_id = _awaiting_with_human_patch(sessions)
    ctx = _ctx(sessions, None, validator=_validator(False))
    with sessions() as s:
        handle_validate(ctx, s, {"attempt_id": att_id, "profile": "database_specialist"})
    with sessions() as s:
        assert s.get_one(Incident, inc_id).state == "ESCALATED"
