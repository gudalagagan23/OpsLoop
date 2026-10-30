import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from opsloop.api.main import app, get_session
from opsloop.core.config import Settings
from opsloop.core.models import Incident, RemediationAttempt, Report
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.testloop.patchutil import make_diff

pytestmark = pytest.mark.integration

APPROVER = {"Authorization": "Bearer tok-approver"}
VIEWER = {"Authorization": "Bearer tok-viewer"}
GOOD = make_diff("taskflow/api/main.py", "a = 1\n", "a = 2\n")


@pytest.fixture
def client(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        api_tokens={
            "tok-approver": {"name": "alice", "role": "approver"},
            "tok-viewer": {"name": "vic", "role": "viewer"},
        },
    )
    monkeypatch.setattr("opsloop.api.main.get_settings", lambda: settings)
    app.dependency_overrides[get_session] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _awaiting(db: Session) -> tuple[int, str]:
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
    for st in ("INVESTIGATING", "REPORT1_READY", "FIXING", "VALIDATING", "AWAITING_APPROVAL"):
        mgr.transition(inc, st, actor="test")
    vh = uuid.uuid4().hex + uuid.uuid4().hex
    db.add(
        RemediationAttempt(
            incident_id=inc.id,
            attempt_no=1,
            strategy="code_fix",
            specialist="api_specialist",
            status="passed",
            patch=GOOD,
            version_hash=vh,
        )
    )
    db.add(
        Report(
            incident_id=inc.id, kind="report2", version=1, body={"verdict": "passed", "diff": GOOD}
        )
    )
    db.commit()
    return inc.id, vh


def _post(client: TestClient, inc: int, headers: dict[str, str], **body: Any) -> Any:
    return client.post(f"/incidents/{inc}/approval", json=body, headers=headers)


def test_anonymous_and_viewer_cannot_decide(db: Session, client: TestClient) -> None:
    inc, vh = _awaiting(db)
    assert _post(client, inc, {}, decision="approve", version_hash=vh).status_code == 401
    assert _post(client, inc, VIEWER, decision="approve", version_hash=vh).status_code == 403
    assert db.get_one(Incident, inc).state == "AWAITING_APPROVAL"
    assert client.get("/me", headers=VIEWER).json() == {"name": "vic", "role": "viewer"}


def test_approver_approves_the_exact_version(db: Session, client: TestClient) -> None:
    inc, vh = _awaiting(db)
    r = _post(client, inc, APPROVER, decision="approve", version_hash=vh)
    assert r.status_code == 200 and r.json()["state"] == "APPROVED"
    assert r.json()["expires_at"]


def test_stale_hash_is_a_conflict(db: Session, client: TestClient) -> None:
    inc, _vh = _awaiting(db)
    r = _post(client, inc, APPROVER, decision="approve", version_hash="e" * 64)
    assert r.status_code == 409 and "stale" in r.json()["detail"]


def test_reject_requires_a_reason(db: Session, client: TestClient) -> None:
    inc, vh = _awaiting(db)
    r = _post(client, inc, APPROVER, decision="reject", version_hash=vh, follow_up="retry")
    assert r.status_code == 409 and "reason" in r.json()["detail"]
    ok = _post(
        client, inc, APPROVER, decision="reject", version_hash=vh, follow_up="retry", reason="no"
    )
    assert ok.status_code == 200 and ok.json()["state"] == "FIXING"


def test_modify_goes_back_to_validation_and_policy_blocks_test_edits(
    db: Session, client: TestClient
) -> None:
    inc, vh = _awaiting(db)
    evil = make_diff("sandbox_tests/visible/integration/test_api.py", "x\n", "y\n")
    r = _post(client, inc, APPROVER, decision="modify", version_hash=vh, patch=evil)
    assert r.status_code == 422 and "test_edit" in r.json()["detail"]
    assert db.get_one(Incident, inc).state == "AWAITING_APPROVAL"
    ok = _post(
        client,
        inc,
        APPROVER,
        decision="modify",
        version_hash=vh,
        patch=GOOD.replace("a = 2", "a = 5"),
    )
    assert ok.status_code == 200 and ok.json()["state"] == "VALIDATING"
    assert db.get_one(Incident, inc).state == "VALIDATING"


def test_modify_without_a_patch_and_unknown_decisions_are_rejected(
    db: Session, client: TestClient
) -> None:
    inc, vh = _awaiting(db)
    assert _post(client, inc, APPROVER, decision="modify", version_hash=vh).status_code == 422
    assert _post(client, inc, APPROVER, decision="maybe", version_hash=vh).status_code == 422
    assert _post(client, 999999, APPROVER, decision="approve", version_hash=vh).status_code == 404


def test_take_over_patch_endpoint_only_works_for_escalated_incidents(
    db: Session, client: TestClient
) -> None:
    inc, vh = _awaiting(db)
    r = client.post(f"/incidents/{inc}/patch", json={"patch": GOOD}, headers=APPROVER)
    assert r.status_code == 409
    _post(
        client,
        inc,
        APPROVER,
        decision="reject",
        version_hash=vh,
        follow_up="take_over",
        reason="mine",
    )
    ok = client.post(f"/incidents/{inc}/patch", json={"patch": GOOD}, headers=APPROVER)
    assert ok.status_code == 200 and ok.json()["state"] == "VALIDATING"


def test_incident_detail_exposes_what_the_approval_popup_needs(
    db: Session, client: TestClient
) -> None:
    inc, vh = _awaiting(db)
    body = client.get(f"/incidents/{inc}").json()
    assert body["report2"]["body"]["diff"] == GOOD
    assert body["attempts"][0]["version_hash"] == vh
    assert body["approval"]["pending_version_hash"] == vh
    _post(client, inc, APPROVER, decision="approve", version_hash=vh)
    after = client.get(f"/incidents/{inc}").json()
    assert [d["decision"] for d in after["approval"]["decisions"]] == ["approve"]
    assert client.get("/approvals/rates").json()["counts"]["approve"] >= 1
