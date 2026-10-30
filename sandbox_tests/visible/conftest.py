"""Visible tests for TaskFlow. Run inside the sandbox against its own throwaway Postgres."""

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from taskflow.api.main import create_app
from taskflow.api.seed import SIM_PASSWORD
from taskflow.config import TaskflowSettings


@pytest.fixture(scope="session")
def settings() -> TaskflowSettings:
    return TaskflowSettings(
        database_url=os.environ["TASKFLOW_DATABASE_URL"],
        seed_tenants=3,
        seed_projects_per_tenant=2,
        seed_tasks_per_project=120,
        log_to_db=False,
        pool_size=3,
        pool_timeout_s=0.5,
    )


@pytest.fixture(scope="session")
def app(settings: TaskflowSettings) -> Iterator[FastAPI]:
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS taskflow CASCADE"))
    engine.dispose()
    yield create_app(settings)


@pytest.fixture(scope="session")
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"email": "user1@tenant1.sim", "password": SIM_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
