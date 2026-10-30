"""Replay probe: runs INSIDE the sandbox, against the patched TaskFlow code in /work.

Seeds a throwaway database, drives a fixed request sequence through the app in-process, and
prints one JSON line of metrics. The faulty code paths are armed through TASKFLOW_FLAGS.
Not importable from the agent's workspace: it is copied to /opt/probe/ by the Test Loop.
"""

import json
import os
import statistics
import sys
import time
from collections import defaultdict
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text

from taskflow.api.main import create_app
from taskflow.api.seed import SIM_PASSWORD
from taskflow.api.telemetry import _query_name
from taskflow.config import TaskflowSettings


def p95(values: list[float]) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def main() -> None:
    url = os.environ["TASKFLOW_DATABASE_URL"]
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS taskflow CASCADE"))
    admin.dispose()

    settings = TaskflowSettings(
        database_url=url,
        seed_tenants=2,
        seed_projects_per_tenant=50,
        seed_tasks_per_project=2_000,
        log_to_db=False,
        pool_size=3,
        pool_timeout_s=0.2,
    )
    app = create_app(settings)
    per_query: dict[str, list[float]] = defaultdict(list)
    state = {"n": 0}
    started: list[float] = []

    def before(*_a: Any) -> None:
        started.append(time.perf_counter())

    def after(*_a: Any) -> None:
        per_query[_query_name.get()].append((time.perf_counter() - started.pop()) * 1000)
        state["n"] += 1

    with TestClient(app) as client:
        engine = app.state.engine
        event.listen(engine, "before_cursor_execute", before)
        event.listen(engine, "after_cursor_execute", after)
        r = client.post(
            "/auth/login", json={"email": "user1@tenant1.sim", "password": SIM_PASSWORD}
        )
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        list_ms: list[float] = []
        list_queries: list[float] = []
        statuses: list[int] = []
        for i in range(40):
            params = {"status": ("todo", "done", "review", "in_progress")[i % 4]} if i % 2 else {}
            n0, t0 = state["n"], time.perf_counter()
            resp = client.get("/projects/1/tasks", params=params, headers=headers)
            list_ms.append((time.perf_counter() - t0) * 1000)
            list_queries.append(state["n"] - n0)
            statuses.append(resp.status_code)
        for _ in range(40):
            statuses.append(client.get("/projects", headers=headers).status_code)

    tasks_q = per_query.get("tasks_by_project_status", []) + per_query.get("tasks_by_project", [])
    out = {
        "requests": len(statuses),
        "errors": sum(1 for s in statuses if s >= 500),
        "pool_timeouts": sum(1 for s in statuses if s == 503),
        "task_list_p95_ms": round(p95(list_ms), 2),
        "task_list_median_ms": round(statistics.median(list_ms), 2),
        "queries_per_request_p95": p95(list_queries),
        "tasks_query_p95_ms": round(p95(tasks_q), 3),
        "flags": sorted(os.environ.get("TASKFLOW_FLAGS", "").split(",")),
    }
    sys.stdout.write("PROBE_RESULT " + json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
