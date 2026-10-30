from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from taskflow.api.main import create_app
from taskflow.api.models import TASKS_PROJECT_STATUS_INDEX
from taskflow.api.seed import SIM_PASSWORD
from taskflow.config import TaskflowSettings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app(db_engine: Engine) -> FastAPI:
    settings = TaskflowSettings(
        database_url=db_engine.url.render_as_string(hide_password=False),
        seed_tenants=3,
        seed_projects_per_tenant=2,
        seed_tasks_per_project=120,
        log_to_db=False,
        pool_size=3,
        pool_timeout_s=0.5,
        pool_leak_interval_s=0.05,
    )
    return create_app(settings)


@pytest.fixture(scope="module")
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"email": "user1@tenant1.sim", "password": SIM_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _queries_for(client: TestClient, auth: dict[str, str]) -> int:
    from taskflow.api.telemetry import DB_QUERIES_PER_REQUEST

    def total() -> float:
        for metric in DB_QUERIES_PER_REQUEST.collect():
            for s in metric.samples:
                if s.name.endswith("_sum") and s.labels["route"] == "/projects/{project_id}/tasks":
                    return s.value
        return 0.0

    before = total()
    r = client.get("/projects/1/tasks", headers=auth)
    assert r.status_code == 200
    assert len(r.json()) == 50
    return int(total() - before)


def test_login_rejects_bad_password(client: TestClient) -> None:
    r = client.post("/auth/login", json={"email": "user1@tenant1.sim", "password": "nope"})
    assert r.status_code == 401


def test_tenant_isolation(client: TestClient, auth: dict[str, str]) -> None:
    projects = client.get("/projects", headers=auth).json()
    assert {p["id"] for p in projects} == {1, 2}
    assert client.get("/projects/3/tasks", headers=auth).status_code == 404


def test_task_crud(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/projects/1/tasks", json={"title": "t"}, headers=auth).json()
    r = client.patch(f"/tasks/{created['id']}", json={"status": "done"}, headers=auth)
    assert r.json()["status"] == "done"
    assert client.get(f"/tasks/{created['id']}", headers=auth).json()["status"] == "done"


def test_fault_1_causes_n_plus_one(app: FastAPI, client: TestClient, auth: dict[str, str]) -> None:
    normal = _queries_for(client, auth)
    app.state.registry.set("1", True)
    try:
        faulty = _queries_for(client, auth)
    finally:
        app.state.registry.set("1", False)
    assert normal <= 3
    assert faulty >= 100


def test_fault_3_drops_and_restores_index(app: FastAPI, db_engine: Engine) -> None:
    def has_index() -> bool:
        with db_engine.connect() as c:
            return bool(
                c.execute(
                    text("SELECT 1 FROM pg_indexes WHERE schemaname='taskflow' AND indexname=:i"),
                    {"i": TASKS_PROJECT_STATUS_INDEX},
                ).first()
            )

    assert has_index()
    app.state.registry.set("3", True)
    assert not has_index()
    app.state.registry.set("3", False)
    assert has_index()


def test_fault_4_exhausts_pool(app: FastAPI, client: TestClient, auth: dict[str, str]) -> None:
    import time

    app.state.registry.set("4", True)
    try:
        time.sleep(0.5)  # leak all 3 connections
        r = client.get("/projects", headers=auth)
        assert r.status_code == 503
        assert client.get("/health").status_code == 503
    finally:
        app.state.registry.set("4", False)
    assert client.get("/projects", headers=auth).status_code == 200


def test_metrics_exposed(client: TestClient) -> None:
    body = client.get("/metrics").text
    for name in ("http_requests_total", "db_query_duration_seconds", "db_pool_in_use"):
        assert name in body
