"""Investigator agent.

Stage 1 ships only a deterministic stub that assembles Report 1 from ledger evidence so the
dashboard and lifecycle can be exercised. The LLM + RAG Investigator replaces it in Stage 2.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsloop.core import ledger
from opsloop.core.models import Evidence, Incident, IncidentEvent, Report, Signal
from opsloop.incidents.manager import IncidentManager

_CATEGORY = {
    "api": "api",
    "database": "database",
    "auth": "auth",
    "billing": "billing",
    "infra": "infra",
    "ux": "ux",
}


def generate_report1_stub(session: Session, mgr: IncidentManager, incident: Incident) -> Report:
    # STUB: no root-cause analysis; summarises observed signals and cites their evidence IDs.
    mgr.transition(incident, "INVESTIGATING", actor="investigator-stub")
    signals = (
        session.execute(select(Signal).where(Signal.incident_id == incident.id)).scalars().all()
    )
    evidence = session.execute(
        select(Evidence.id, Evidence.type).where(Evidence.incident_id == incident.id)
    ).all()
    deploy_ids = [eid for eid, etype in evidence if etype == "deploy_event"]
    body: dict[str, Any] = {
        "incident_id": incident.id,
        "summary": "; ".join(
            f"{s.signal_id} {_fmt(s.labels)} = {s.value:.4g}"
            + (f" (baseline {s.baseline:.4g})" if s.baseline is not None else "")
            for s in signals
        ),
        "suspected_root_cause": {
            "description": "Not analysed: Stage 1 stub report.",
            "category": _CATEGORY.get(incident.wing, "unknown"),
            "confidence": 0.0,
        },
        "evidence_for": [s.evidence_id for s in signals if s.evidence_id is not None],
        "evidence_against": [],
        "affected_services": sorted({s.service for s in signals}),
        "affected_tenants": {"count": 0, "plans": []},
        "deployment_correlation": {
            "deploy_event_id": deploy_ids[0] if deploy_ids else None,
            "relation": "unknown" if deploy_ids else "none",
        },
        "uncertainty": "Stub: confidence is 0 because no investigation was performed.",
        "recommended_strategies": [
            {
                "strategy": "escalate",
                "rationale": "Confidence is below the escalation threshold (0.6).",
                "risk": "low",
            }
        ],
        "sources": [],
        "stub": True,
    }
    ev = ledger.append(
        session,
        type="hypothesis",
        source="investigator-stub",
        incident_id=incident.id,
        payload={"report": "report1", "stub": True, "summary": body["summary"]},
    )
    version = (
        session.execute(
            select(func.coalesce(func.max(Report.version), 0)).where(
                Report.incident_id == incident.id, Report.kind == "report1"
            )
        ).scalar_one()
        + 1
    )
    report = Report(
        incident_id=incident.id,
        kind="report1",
        version=version,
        body=body,
        is_stub=True,
        evidence_id=ev.id,
    )
    session.add(report)
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            ts=mgr.now(),
            kind="report_ready",
            actor="investigator-stub",
            reason="Report 1 ready",
            evidence_id=ev.id,
        )
    )
    mgr.transition(incident, "REPORT1_READY", actor="investigator-stub")
    session.flush()
    return report


def _fmt(labels: dict[str, str]) -> str:
    return "{" + ",".join(f"{k}={v}" for k, v in sorted(labels.items())) + "}" if labels else ""
