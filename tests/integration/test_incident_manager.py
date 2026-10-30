from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsloop.agents.investigator import generate_report1_stub
from opsloop.core import ledger
from opsloop.core.models import DeployEvent, Incident, IncidentEvent, Report, Signal
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.incidents.state_machine import IllegalTransition

pytestmark = pytest.mark.integration


def _fired(
    signal_id: str = "db.pool_saturation",
    service: str = "postgres",
    watcher: str = "database",
    severity: str = "S2",
    value: float = 1.0,
    series_key: str = "",
    at: datetime | None = None,
) -> FiredSignal:
    at = at or datetime.now(UTC)
    return FiredSignal(
        watcher=watcher,
        signal_id=signal_id,
        series_key=series_key,
        labels={},
        service=service,
        severity=severity,
        method="threshold",
        query="q",
        value=value,
        baseline=0.2,
        condition_since=at - timedelta(seconds=30),
        fired_at=at,
        title=f"{signal_id} firing",
    )


def _tag(request: pytest.FixtureRequest) -> str:
    return request.node.name[:60]


def test_first_signal_opens_and_triages(db: Session, request: pytest.FixtureRequest) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    inc = mgr.ingest(_fired(signal_id="t1.sig", series_key="a"))
    db.commit()
    assert inc.state == "TRIAGED"
    assert inc.severity in {"S1", "S2", "S3", "S4"}
    kinds = (
        db.execute(select(IncidentEvent.kind).where(IncidentEvent.incident_id == inc.id))
        .scalars()
        .all()
    )
    assert kinds[0] == "opened"
    assert "state_change" in kinds


def test_repeat_signal_is_deduplicated(db: Session, request: pytest.FixtureRequest) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    first = mgr.ingest(_fired(signal_id="t2.sig", series_key="k", value=0.9))
    again = mgr.ingest(_fired(signal_id="t2.sig", series_key="k", value=0.95))
    db.commit()
    assert first.id == again.id
    rows = db.execute(select(Signal).where(Signal.signal_id == "t2.sig")).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == 0.95
    assert first.signal_count == 1


def test_related_services_correlate_into_one_incident(
    db: Session, request: pytest.FixtureRequest
) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    db_inc = mgr.ingest(_fired(signal_id="t3.pool", service="postgres", series_key="x"))
    api_inc = mgr.ingest(
        _fired(
            signal_id="t3.error_rate",
            service="taskflow-api",
            watcher="api",
            series_key="x",
            severity="S1",
            value=0.3,
        )
    )
    db.commit()
    assert db_inc.id == api_inc.id
    assert api_inc.signal_count == 2
    assert api_inc.severity in {"S1", "S2"}


def test_unrelated_service_opens_new_incident(db: Session, request: pytest.FixtureRequest) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    a = mgr.ingest(_fired(signal_id="t4.a", service="postgres", series_key="1"))
    b = mgr.ingest(
        _fired(signal_id="t4.b", service="payments-sim", watcher="billing", series_key="1")
    )
    db.commit()
    assert a.id != b.id


def test_cleared_signal_fires_again_as_new_row(db: Session, request: pytest.FixtureRequest) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    mgr.ingest(_fired(signal_id="t5.sig", series_key="z"))
    mgr.clear("t5.sig", "z", datetime.now(UTC))
    mgr.ingest(_fired(signal_id="t5.sig", series_key="z"))
    db.commit()
    rows = db.execute(select(Signal).where(Signal.signal_id == "t5.sig")).scalars().all()
    assert len(rows) == 2


def test_signal_still_firing_after_dismissal_opens_new_incident(
    db: Session, request: pytest.FixtureRequest
) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    first = mgr.ingest(_fired(signal_id="t9.sig", series_key="s", service="ext-t9"))
    mgr.transition(first, "DISMISSED", actor="test", reason="not a bug")
    second = mgr.ingest(_fired(signal_id="t9.sig", series_key="s", service="ext-t9"))
    db.commit()
    assert second.id != first.id
    assert second.state == "TRIAGED"


def test_illegal_transition_raises(db: Session, request: pytest.FixtureRequest) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    inc = mgr.ingest(_fired(signal_id="t6.sig", series_key="q"))
    with pytest.raises(IllegalTransition):
        mgr.transition(inc, "RESOLVED", actor="test")
    db.rollback()


def test_recent_deploy_is_recorded_as_evidence(db: Session, request: pytest.FixtureRequest) -> None:
    db.add(
        DeployEvent(
            ts=datetime.now(UTC) - timedelta(minutes=5),
            service="taskflow-api",
            version="t7",
            git_sha="abc",
            stage="100%",
            actor="test",
        )
    )
    db.flush()
    mgr = IncidentManager(db, run_tag=_tag(request))
    inc = mgr.ingest(_fired(signal_id="t7.sig", series_key="d", service="ext-t7"))
    db.commit()
    from opsloop.core.models import Evidence

    types = db.execute(select(Evidence.type).where(Evidence.incident_id == inc.id)).scalars().all()
    assert "deploy_event" in types


def test_report1_stub_cites_ledger_evidence(db: Session, request: pytest.FixtureRequest) -> None:
    mgr = IncidentManager(db, run_tag=_tag(request))
    inc = mgr.ingest(_fired(signal_id="t8.sig", series_key="r", service="ext-t8"))
    report = generate_report1_stub(db, mgr, inc)
    db.commit()
    assert inc.state == "REPORT1_READY"
    assert report.is_stub
    assert report.version == 1
    assert report.body["evidence_for"], "Report 1 must cite ledger evidence"
    assert db.execute(select(Report).where(Report.incident_id == inc.id)).scalar_one()
    assert ledger.verify(db).ok
    assert db.get(Incident, inc.id) is not None
