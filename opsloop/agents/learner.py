"""Learner: turn verdicts into better future runs.

Today it does the part the spec allows without a human: a fix the Judge called RESOLVED becomes
a past-incident entry in the knowledge base, so the Investigator retrieves it the next time the
same symptoms appear (spec section 14: only RESOLVED fixes are added). Detection-threshold
proposals with backtesting are the next step and need human approval before they apply."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsloop.core import ledger
from opsloop.core.embedder import Embedder
from opsloop.core.models import Incident, Outcome, Report
from opsloop.incidents.manager import IncidentManager
from opsloop.knowledge.ingest import add_resolved_incident

log = logging.getLogger("opsloop.learner")


def _latest_report(session: Session, incident_id: int, kind: str) -> Report | None:
    return session.execute(
        select(Report)
        .where(Report.incident_id == incident_id, Report.kind == kind)
        .order_by(Report.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def learn_from_resolved(session: Session, embedder: Embedder, incident_id: int) -> int | None:
    """Add a RESOLVED incident and its fix to the knowledge base. Returns the chunk id."""
    outcome = (
        session.execute(
            select(Outcome).where(Outcome.incident_id == incident_id).order_by(Outcome.id.desc())
        )
        .scalars()
        .first()
    )
    if outcome is None or outcome.verdict != "RESOLVED":
        return None  # spec: only RESOLVED fixes are added
    inc = session.get_one(Incident, incident_id)
    r1 = _latest_report(session, incident_id, "report1")
    r2 = _latest_report(session, incident_id, "report2")
    if r1 is None or r2 is None:
        return None
    cause = r1.body.get("suspected_root_cause", {})
    summary = (
        f"{inc.title}. Root cause: {cause.get('description', '?')} ({cause.get('category', '?')})."
    )
    changed = [
        ln[6:] for ln in str(r2.body.get("diff", "")).splitlines() if ln.startswith("+++ b/")
    ]
    fix = (
        f"strategy {r2.body.get('strategy')}, changed {', '.join(changed) or 'code'}; "
        f"validated in the sandbox and released 5% -> 25% -> 100%; Judge: {outcome.explanation}"
    )
    row = add_resolved_incident(
        session, embedder, incident_id=incident_id, wing=inc.wing, summary=summary, fix=fix[:1500]
    )
    if row is None:
        return None  # already learned
    ev = ledger.append(
        session,
        type="action",
        source="learner",
        incident_id=incident_id,
        payload={"action": "knowledge_added", "chunk_id": row.id, "title": row.title},
    )
    IncidentManager(session).note(
        inc,
        "learned",
        actor="learner",
        reason="added this incident and its validated fix to the knowledge base",
        evidence_id=ev.id,
    )
    log.info("incident %s: learned (knowledge chunk %s)", incident_id, row.id)
    return row.id
