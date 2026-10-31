import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from opsloop.api.deps import get_session
from opsloop.api.main import app
from opsloop.incidents.manager import FiredSignal, IncidentManager

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _incident(db: Session, wing: str = "database") -> int:
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    inc = IncidentManager(db, run_tag=tag).ingest(
        FiredSignal(
            watcher=wing,
            signal_id=f"{wing}.x",
            series_key=tag,
            labels={},
            service=f"svc-{tag}",
            severity="S1",
            method="threshold",
            query="q",
            value=1,
            baseline=0,
            condition_since=now - timedelta(seconds=30),
            fired_at=now,
            title=f"{wing} problem",
        )
    )
    db.commit()
    return inc.id


def test_overview_counts_active_incidents_and_pipeline(db: Session, client: TestClient) -> None:
    _incident(db)
    body = client.get("/overview").json()
    assert body["simulated"] is True and body["incidents"]["active"] >= 1
    assert [s["key"] for s in body["pipeline"]] == [
        "detect",
        "triage",
        "investigate",
        "fix",
        "validate",
        "approve",
        "release",
        "judge",
        "learn",
    ]
    assert body["pipeline"][1]["count"] >= 1  # the new incident sits in triage
    assert set(body["incidents"]["by_severity"]) == {"S1", "S2", "S3", "S4"}


def test_departments_cover_all_six_wings_and_say_which_are_monitored(
    db: Session, client: TestClient
) -> None:
    _incident(db, wing="database")
    rows = {d["wing"]: d for d in client.get("/departments").json()}
    assert set(rows) == {"api", "database", "auth", "billing", "infra", "ux"}
    assert rows["database"]["status"] == "incident" and rows["database"]["active_incidents"]
    assert rows["billing"]["status"] == "not_monitored" and rows["billing"]["planned_stage"]
    assert rows["api"]["specialist"] == "api_specialist"


def test_agents_lists_every_component_with_an_honest_status(client: TestClient) -> None:
    body = client.get("/agents").json()
    keys = [c["key"] for c in body["components"]]
    assert {"watchers", "investigator", "sandbox", "approval", "deploy", "judge", "learner"} <= set(
        keys
    )
    learner = next(c for c in body["components"] if c["key"] == "learner")
    assert "fixes learned" in learner["detail"]
    assert all(
        c["status"] in {"active", "ready", "working", "waiting", "planned", "down"}
        for c in body["components"]
    )


def test_activity_approvals_ledger_llm_releases_signals(db: Session, client: TestClient) -> None:
    inc = _incident(db)
    acts = client.get("/activity?limit=5").json()
    assert acts and {"kind", "ts", "incident"} <= set(acts[0])
    assert any(a["incident"]["id"] == inc for a in client.get("/activity?limit=50").json())
    ap = client.get("/approvals").json()
    assert {"pending", "history", "rates"} <= set(ap)
    led = client.get("/ledger/recent?limit=3").json()
    assert len(led) == 3 and all(len(e["hash"]) == 64 for e in led)
    assert isinstance(client.get("/llm/calls").json(), list)
    assert set(client.get("/releases").json()) == {"deployments", "outcomes"}
    assert isinstance(client.get("/signals/recent").json(), list)


def test_live_metrics_reports_unavailable_instead_of_failing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opsloop.core.config import Settings

    monkeypatch.setattr(
        "opsloop.api.dashboard.get_settings",
        lambda: Settings(_env_file=None, prometheus_url="http://127.0.0.1:9"),
    )
    body = client.get("/metrics/live").json()
    assert body["available"] is False and body["series"] == {}


def test_simulation_toggles_need_the_approver_role(client: TestClient) -> None:
    assert client.post("/simulation/faults/1/on").status_code == 401
    assert client.post("/simulation/reset").status_code == 401
