"""Human approval. Approval is bound to the exact validated version hash:

* approve  -> stores {approver, time, version_hash, expiry}; only a hash match counts.
* modify   -> the human's edited patch gets a NEW hash and goes back to VALIDATING; nothing
              approved earlier can match it.
* reject   -> needs a reason; retry, change strategy, or take over (human patch, same checks).

Every decision is written to the ledger. Plain code, no AI."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsloop.agents.profiles import SpecialistProfile, load_all
from opsloop.core import jobs, ledger
from opsloop.core.models import Approval, Incident, RemediationAttempt
from opsloop.incidents.manager import IncidentManager
from opsloop.testloop.diffpolicy import check_patch

log = logging.getLogger("opsloop.approvals")

FOLLOW_UPS = ("retry", "change_strategy", "take_over")


class ApprovalError(Exception):
    """The request is not allowed in the incident's current state."""


class PatchRejected(ApprovalError):
    """A human-written patch violates the diff policy."""


def latest_passed_attempt(session: Session, incident_id: int) -> RemediationAttempt | None:
    return session.execute(
        select(RemediationAttempt)
        .where(RemediationAttempt.incident_id == incident_id, RemediationAttempt.status == "passed")
        .order_by(RemediationAttempt.attempt_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def expect_pending(session: Session, incident_id: int, version_hash: str) -> RemediationAttempt:
    inc = session.get_one(Incident, incident_id)
    if inc.state != "AWAITING_APPROVAL":
        raise ApprovalError(f"incident is {inc.state}, not AWAITING_APPROVAL")
    att = latest_passed_attempt(session, incident_id)
    if att is None or att.version_hash != version_hash:
        raise ApprovalError("stale decision: the proposed version changed; reload and review it")
    return att


def _record(
    session: Session,
    *,
    incident_id: int,
    version_hash: str,
    decision: str,
    approver: str,
    reason: str | None = None,
    follow_up: str | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    ev = ledger.append(
        session,
        type="approval",
        source=f"human:{approver}",
        incident_id=incident_id,
        payload={
            "decision": decision,
            "approver": approver,
            "version_hash": version_hash,
            "reason": reason,
            "follow_up": follow_up,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    row = Approval(
        incident_id=incident_id,
        version_hash=version_hash,
        decision=decision,
        approver=approver,
        reason=reason,
        follow_up=follow_up,
        expires_at=expires_at,
        evidence_id=ev.id,
    )
    session.add(row)
    session.flush()
    return row


def approve(
    session: Session,
    incident_id: int,
    version_hash: str,
    approver: str,
    *,
    ttl_s: float,
    now: datetime | None = None,
) -> Approval:
    now = now or datetime.now(UTC)
    expect_pending(session, incident_id, version_hash)
    row = _record(
        session,
        incident_id=incident_id,
        version_hash=version_hash,
        decision="approve",
        approver=approver,
        expires_at=now + timedelta(seconds=ttl_s),
    )
    mgr = IncidentManager(session)
    inc = session.get_one(Incident, incident_id)
    mgr.transition(
        inc, "APPROVED", actor=f"human:{approver}", reason=f"approved {version_hash[:12]}"
    )
    # The release (staged rollout, then the Judge) runs in the worker, for this exact version.
    jobs.enqueue(
        session,
        "release",
        {"incident_id": incident_id, "version_hash": version_hash},
        idempotency_key=f"release:{incident_id}:{version_hash[:16]}",
        max_attempts=1,  # a half-run rollout is not retried blindly: a human looks first
    )
    return row


def reject(
    session: Session,
    incident_id: int,
    version_hash: str,
    approver: str,
    reason: str,
    follow_up: str,
) -> Approval:
    if not reason or not reason.strip():
        raise ApprovalError("a reason is required to reject")
    if follow_up not in FOLLOW_UPS:
        raise ApprovalError(f"follow_up must be one of {FOLLOW_UPS}")
    att = expect_pending(session, incident_id, version_hash)
    row = _record(
        session,
        incident_id=incident_id,
        version_hash=version_hash,
        decision="reject",
        approver=approver,
        reason=reason.strip(),
        follow_up=follow_up,
    )
    mgr = IncidentManager(session)
    inc = session.get_one(Incident, incident_id)
    actor = f"human:{approver}"
    if follow_up == "take_over":
        mgr.transition(inc, "ESCALATED", actor=actor, reason=f"human takes over: {reason.strip()}")
        return row
    mgr.transition(inc, "FIXING", actor=actor, reason=f"rejected, {follow_up}: {reason.strip()}")
    # A fresh remediation run, carrying the human's note (and the ruled-out strategy).
    jobs.enqueue(
        session,
        "remediate",
        {
            "incident_id": incident_id,
            "run_id": f"r{row.id}",
            "notes": [f"A human rejected the previous fix: {reason.strip()}"],
            "exclude": [att.strategy] if follow_up == "change_strategy" else [],
        },
        idempotency_key=f"remediate:{incident_id}:r{row.id}",
    )
    return row


def submit_patch(
    session: Session,
    incident_id: int,
    patch: str,
    author: str,
    profile: SpecialistProfile,
    *,
    kind: str,
) -> RemediationAttempt:
    """A human-written patch (an edit during approval, or a take-over fix). It faces the same
    policy and validation as an agent's: a new hash, a new Test Loop run, a fresh approval."""
    inc = session.get_one(Incident, incident_id)
    if kind == "modify" and inc.state != "AWAITING_APPROVAL":
        raise ApprovalError(f"incident is {inc.state}, not AWAITING_APPROVAL")
    if kind == "take_over" and inc.state != "ESCALATED":
        raise ApprovalError(f"incident is {inc.state}, not ESCALATED")
    policy = check_patch(patch, profile)
    if not policy.ok:
        raise PatchRejected(
            "patch rejected by policy: "
            + "; ".join(f"{v.rule} ({v.detail})" for v in policy.violations[:4])
        )
    previous = latest_passed_attempt(session, incident_id)
    n = (
        session.execute(
            select(func.coalesce(func.max(RemediationAttempt.attempt_no), 0)).where(
                RemediationAttempt.incident_id == incident_id
            )
        ).scalar_one()
        + 1
    )
    att = RemediationAttempt(
        incident_id=incident_id,
        attempt_no=n,
        strategy="code_fix",
        specialist=f"human:{author}",
        status="pending_validation",
        patch=patch if patch.endswith("\n") else patch + "\n",
    )
    session.add(att)
    session.flush()
    mgr = IncidentManager(session)
    actor = f"human:{author}"
    if kind == "modify" and previous is not None and previous.version_hash:
        _record(
            session,
            incident_id=incident_id,
            version_hash=previous.version_hash,
            decision="modify",
            approver=author,
            reason="human edited the proposed patch; the previous version is void",
        )
    ledger.append(
        session,
        type="patch",
        source=actor,
        incident_id=incident_id,
        payload={"attempt": n, "patch": att.patch, "kind": kind},
    )
    if kind == "take_over":
        mgr.transition(inc, "FIXING", actor=actor, reason="human fix submitted")
    mgr.transition(inc, "VALIDATING", actor=actor, reason=f"validating {kind} patch (attempt {n})")
    jobs.enqueue(
        session,
        "validate",
        {"attempt_id": att.id, "profile": profile.name},
        idempotency_key=f"validate:{att.id}",
    )
    return att


def require_valid_approval(
    session: Session, incident_id: int, version_hash: str, now: datetime | None = None
) -> Approval:
    """Gate for the deployment controller: the NEWEST decision on this incident must be an
    unexpired approval of exactly this version."""
    now = now or datetime.now(UTC)
    newest = session.execute(
        select(Approval)
        .where(Approval.incident_id == incident_id)
        .order_by(Approval.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if newest is None or newest.decision != "approve":
        raise ApprovalError("no standing approval for this incident")
    if newest.version_hash != version_hash:
        raise ApprovalError("approval does not match this version hash")
    if newest.expires_at is not None and newest.expires_at <= now:
        raise ApprovalError("approval has expired")
    return newest


def approval_summary(session: Session, incident_id: int) -> dict[str, Any]:
    pending = latest_passed_attempt(session, incident_id)
    decisions = session.execute(
        select(Approval).where(Approval.incident_id == incident_id).order_by(Approval.id)
    ).scalars()
    return {
        "pending_version_hash": pending.version_hash if pending else None,
        "decisions": [
            {
                "id": a.id,
                "decision": a.decision,
                "approver": a.approver,
                "version_hash": a.version_hash,
                "reason": a.reason,
                "follow_up": a.follow_up,
                "created_at": a.created_at.isoformat(),
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in decisions
        ],
    }


def decision_rates(session: Session) -> dict[str, Any]:
    """Approve / modify / reject rates (evaluation metric, spec section 16)."""
    rows = session.execute(
        select(Approval.decision, func.count()).group_by(Approval.decision)
    ).all()
    counts = {d: int(n) for d, n in rows}
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "rates": {d: (n / total if total else 0.0) for d, n in counts.items()},
    }


def profile_for_incident(session: Session, incident_id: int) -> SpecialistProfile:
    """Write scope for a human patch: the specialist that last worked on the incident (so a
    human cannot widen the scope), else the generic one."""
    profiles = load_all()
    prev = session.execute(
        select(RemediationAttempt.specialist)
        .where(
            RemediationAttempt.incident_id == incident_id,
            ~RemediationAttempt.specialist.startswith("human:"),
        )
        .order_by(RemediationAttempt.attempt_no.desc())
        .limit(1)
    ).scalar_one_or_none()
    return profiles[prev] if prev in profiles else profiles["generic_specialist"]
