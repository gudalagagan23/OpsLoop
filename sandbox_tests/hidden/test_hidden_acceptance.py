"""Hidden checks. The coding agent never sees these; they are copied into the validation
sandbox only after the patch is applied. They encode the acceptance criteria independently
of the replay probe, so a patch can't just game one measurement."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text

from taskflow.api.main import create_app
from taskflow.api.seed import SIM_PASSWORD
from taskflow.config import TaskflowSettings


@pytest.fixture(scope="module")
def settings() -> TaskflowSettings:
    return TaskflowSettings(
        database_url=os.environ["TASKFLOW_DATABASE_URL"],
        seed_tenants=2,
        seed_projects_per_tenant=2,
        seed_tasks_per_project=300,
        log_to_db=False,
        pool_size=3,
        pool_timeout_s=0.3,
    )


@pytest.fixture(scope="module")
def client(settings: TaskflowSettings) -> Iterator[TestClient]:
    admin = create_engine(settings.database_url)
    with admin.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS taskflow CASCADE"))
    admin.dispose()
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"email": "user1@tenant1.sim", "password": SIM_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_task_list_uses_a_constant_number_of_queries(
    client: TestClient, auth: dict[str, str]
) -> None:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    count = {"n": 0}

    def bump(*_a: object) -> None:
        count["n"] += 1

    event.listen(engine, "before_cursor_execute", bump)
    try:
        r = client.get("/projects/1/tasks", headers=auth)
    finally:
        event.remove(engine, "before_cursor_execute", bump)
    assert r.status_code == 200 and len(r.json()) == 50
    assert count["n"] <= 4, f"{count['n']} queries for one list request"


def test_composite_task_index_exists(client: TestClient, settings: TaskflowSettings) -> None:
    eng = create_engine(settings.database_url)
    with eng.connect() as c:
        row = c.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'taskflow' "
                "AND tablename = 'tasks' AND indexdef ILIKE '%(project_id, status)%'"
            )
        ).first()
    eng.dispose()
    assert row is not None, "no (project_id, status) index on taskflow.tasks"


def test_pool_survives_sustained_requests(client: TestClient, auth: dict[str, str]) -> None:
    codes = [client.get("/projects", headers=auth).status_code for _ in range(60)]
    assert codes.count(200) == 60, f"non-200 responses: {sorted(set(codes))}"
