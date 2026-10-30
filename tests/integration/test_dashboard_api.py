import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from opsloop.agents.investigator import generate_report1_stub
from opsloop.api.main import app, get_session
from opsloop.incidents.manager import FiredSignal, IncidentManager

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _open_incident(db: Session) -> int:
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:8]
    mgr = IncidentManager(db, run_tag=f"dashboard-{tag}")
    inc = mgr.ingest(
        FiredSignal(
            watcher="api",
            signal_id="dash.error_rate",
            series_key=tag,
            labels={},
            service=f"dash-{tag}",
            severity="S1",
            method="threshold",
            query="q",
            value=0.2,
            baseline=0.05,
            condition_since=now - timedelta(seconds=30),
            fired_at=now,
            title="API 5xx error rate high",
        )
    )
    generate_report1_stub(db, mgr, inc)
    db.commit()
    return inc.id


def test_board_places_incident_in_its_column(db: Session, client: TestClient) -> None:
    inc_id = _open_incident(db)
    body = client.get("/board").json()
    assert body["simulated"] is True
    names = [c["name"] for c in body["columns"]]
    assert names[0] == "New" and names[-1] == "Closed"
    investigating = next(c for c in body["columns"] if c["name"] == "Investigating")
    assert inc_id in [i["id"] for i in investigating["incidents"]]


def test_incident_detail_has_evidence_chain_and_report(db: Session, client: TestClient) -> None:
    inc_id = _open_incident(db)
    body = client.get(f"/incidents/{inc_id}").json()
    assert body["key"] == f"INC-{inc_id:05d}"
    assert [s["signal_id"] for s in body["signals"]] == ["dash.error_rate"]
    assert body["report1"]["is_stub"] is True
    evidence_ids = {e["id"] for e in body["evidence"]}
    assert set(body["report1"]["body"]["evidence_for"]) <= evidence_ids
    assert client.get("/incidents/999999").status_code == 404


def test_ledger_verify_endpoint(client: TestClient) -> None:
    assert client.get("/ledger/verify").json()["ok"] is True
