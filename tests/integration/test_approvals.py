import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsloop.agents.profiles import load_profile
from opsloop.approvals import service as svc
from opsloop.core import ledger
from opsloop.core.models import Approval, Incident, Job, RemediationAttempt
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.testloop.patchutil import make_diff

pytestmark = pytest.mark.integration

GOOD = make_diff("taskflow/api/main.py", "a = 1\n", "a = 2\n")
API = load_profile("api_specialist")


def _awaiting(db: Session) -> tuple[Incident, RemediationAttempt]:
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    mgr = IncidentManager(db, run_tag=tag)
    inc = mgr.ingest(
        FiredSignal(
            watcher="api",
            signal_id="x",
            series_key=tag,
            labels={},
            service=f"svc-{tag}",
            severity="S2",
            method="threshold",
            query="q",
            value=1,
            baseline=0,
            condition_since=now,
            fired_at=now,
            title="t",
        )
    )
    for state in ("INVESTIGATING", "REPORT1_READY", "FIXING", "VALIDATING", "AWAITING_APPROVAL"):
        mgr.transition(inc, state, actor="test")
    att = RemediationAttempt(
        incident_id=inc.id,
        attempt_no=1,
        strategy="code_fix",
        specialist="api_specialist",
        status="passed",
        patch=GOOD,
        version_hash=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    db.add(att)
    db.commit()
    return inc, att


def test_approve_binds_to_the_exact_hash_and_moves_to_approved(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    row = svc.approve(db, inc.id, att.version_hash, "alice", ttl_s=600)
    db.commit()
    assert inc.state == "APPROVED" and row.expires_at is not None
    assert svc.require_valid_approval(db, inc.id, att.version_hash).id == row.id
    assert ledger.verify(db).ok


def test_approval_of_a_different_hash_is_refused(db: Session) -> None:
    inc, att = _awaiting(db)
    with pytest.raises(svc.ApprovalError, match="stale"):
        svc.approve(db, inc.id, "f" * 64, "alice", ttl_s=600)
    assert inc.state == "AWAITING_APPROVAL"


def test_deployment_gate_rejects_wrong_hash_expired_and_missing_approvals(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    with pytest.raises(svc.ApprovalError, match="no standing approval"):
        svc.require_valid_approval(db, inc.id, att.version_hash)
    svc.approve(db, inc.id, att.version_hash, "alice", ttl_s=60)
    db.commit()
    with pytest.raises(svc.ApprovalError, match="does not match"):
        svc.require_valid_approval(db, inc.id, "0" * 64)
    later = datetime.now(UTC) + timedelta(seconds=61)
    with pytest.raises(svc.ApprovalError, match="expired"):
        svc.require_valid_approval(db, inc.id, att.version_hash, now=later)


def test_reject_needs_a_reason_and_a_valid_follow_up(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    with pytest.raises(svc.ApprovalError, match="reason"):
        svc.reject(db, inc.id, att.version_hash, "bob", "  ", "retry")
    with pytest.raises(svc.ApprovalError, match="follow_up"):
        svc.reject(db, inc.id, att.version_hash, "bob", "no", "shrug")
    assert inc.state == "AWAITING_APPROVAL"


def test_reject_retry_restarts_remediation_with_the_humans_note(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    row = svc.reject(db, inc.id, att.version_hash, "bob", "touches too much", "retry")
    db.commit()
    assert inc.state == "FIXING"
    job = db.execute(
        select(Job).where(
            Job.queue == "remediate", Job.idempotency_key == f"remediate:{inc.id}:r{row.id}"
        )
    ).scalar_one()
    assert job.payload["notes"] == ["A human rejected the previous fix: touches too much"]
    assert job.payload["exclude"] == []
    with pytest.raises(svc.ApprovalError):  # the rejected version can no longer be approved
        svc.approve(db, inc.id, att.version_hash, "bob", ttl_s=60)


def test_reject_change_strategy_excludes_the_rejected_strategy(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    row = svc.reject(db, inc.id, att.version_hash, "bob", "wrong approach", "change_strategy")
    db.commit()
    job = db.execute(
        select(Job).where(Job.idempotency_key == f"remediate:{inc.id}:r{row.id}")
    ).scalar_one()
    assert job.payload["exclude"] == ["code_fix"]


def test_reject_take_over_escalates_and_human_patch_is_revalidated(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    svc.reject(db, inc.id, att.version_hash, "bob", "I will fix it myself", "take_over")
    db.commit()
    assert inc.state == "ESCALATED"
    new = svc.submit_patch(db, inc.id, GOOD, "bob", API, kind="take_over")
    db.commit()
    assert inc.state == "VALIDATING" and new.status == "pending_validation"
    assert new.specialist == "human:bob" and new.attempt_no == 2
    assert db.execute(select(Job).where(Job.idempotency_key == f"validate:{new.id}")).scalar_one()


def test_modify_voids_the_old_version_and_forces_revalidation(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    edited = make_diff("taskflow/api/main.py", "a = 1\n", "a = 3\n")
    new = svc.submit_patch(db, inc.id, edited, "alice", API, kind="modify")
    db.commit()
    assert inc.state == "VALIDATING", "an edited patch must go back through the Test Loop"
    assert new.version_hash is None and new.status == "pending_validation"
    decisions = db.execute(select(Approval).where(Approval.incident_id == inc.id)).scalars().all()
    assert [(d.decision, d.version_hash) for d in decisions] == [("modify", att.version_hash)]
    with pytest.raises(svc.ApprovalError):
        svc.require_valid_approval(db, inc.id, att.version_hash)


def test_modified_patch_that_edits_tests_is_blocked_before_validation(db: Session) -> None:
    inc, _att = _awaiting(db)
    evil = make_diff("sandbox_tests/visible/integration/test_api.py", "x\n", "y\n")
    with pytest.raises(svc.ApprovalError, match="test_edit"):
        svc.submit_patch(db, inc.id, evil, "alice", API, kind="modify")
    db.rollback()
    assert db.get_one(Incident, inc.id).state == "AWAITING_APPROVAL"


def test_decision_rates(db: Session) -> None:
    inc, att = _awaiting(db)
    assert att.version_hash is not None
    svc.approve(db, inc.id, att.version_hash, "alice", ttl_s=60)
    db.commit()
    rates = svc.decision_rates(db)
    assert rates["counts"]["approve"] >= 1 and 0 < rates["rates"]["approve"] <= 1
