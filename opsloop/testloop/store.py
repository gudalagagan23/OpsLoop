"""Persist a validation result: attempt row, test run, Report 2 and ledger entries."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsloop.core import ledger
from opsloop.core.models import RemediationAttempt, Report, TestRun
from opsloop.testloop.loop import Validation


def record_validation(
    session: Session,
    attempt: RemediationAttempt,
    validation: Validation | None,
    *,
    patch: str,
    agent_notes: str = "",
    cost_usd: float = 0.0,
) -> None:
    """Save the outcome of one attempt. `validation` is None when no patch was produced."""
    verdict = validation.verdict if validation else "agent_failed"
    attempt.patch = patch or None
    attempt.notes = agent_notes[:4000]
    attempt.cost_usd = cost_usd
    attempt.status = "passed" if (validation and validation.passed) else verdict
    if validation is None:
        return
    attempt.version_hash = validation.version_hash
    attempt.base_commit = validation.base_commit
    ev = ledger.append(
        session,
        type="test_result",
        source="test-loop",
        incident_id=attempt.incident_id,
        payload={
            "attempt": attempt.attempt_no,
            "verdict": verdict,
            "version_hash": validation.version_hash,
            "gap_closed": round(validation.gap_closed, 3),
            "steps": [
                {"step": x.name, "status": x.status, "seconds": round(x.seconds, 1)}
                for x in validation.steps
            ],
        },
    )
    session.add(
        TestRun(
            incident_id=attempt.incident_id,
            attempt_id=attempt.id,
            version_hash=validation.version_hash,
            passed=validation.passed,
            steps=validation.report2,
        )
    )
    version = (
        session.execute(
            select(func.coalesce(func.max(Report.version), 0)).where(
                Report.incident_id == attempt.incident_id, Report.kind == "report2"
            )
        ).scalar_one()
        + 1
    )
    session.add(
        Report(
            incident_id=attempt.incident_id,
            kind="report2",
            version=version,
            body=validation.report2,
            is_stub=False,
            evidence_id=ev.id,
        )
    )
