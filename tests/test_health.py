from fastapi.testclient import TestClient

from opsloop.api.main import app, get_db_status
from opsloop.core.db import DatabaseStatus


def _client_with(status: DatabaseStatus) -> TestClient:
    app.dependency_overrides[get_db_status] = lambda: status
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_health_ok_when_database_and_pgvector_ready() -> None:
    client = _client_with(DatabaseStatus(reachable=True, pgvector=True))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "pgvector": True}


def test_health_degraded_when_database_unreachable() -> None:
    client = _client_with(DatabaseStatus(reachable=False, pgvector=False))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_health_degraded_when_pgvector_missing() -> None:
    client = _client_with(DatabaseStatus(reachable=True, pgvector=False))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["pgvector"] is False
