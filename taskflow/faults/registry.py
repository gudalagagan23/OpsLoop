"""Planted faults (simulated). Toggled only through the control app on a separate port that
OpsLoop agents can never reach. IDs follow the fault catalog in docs/PROJECT_SPEC.md §14.

Each fault is a bug in TaskFlow code that sits behind a feature flag (see taskflow/flags.py);
switching a fault on flips its flag. Fault 3 also drops the index from the live database."""

import logging
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from sqlalchemy import Connection, Engine, text

from taskflow import flags
from taskflow.api.models import SCHEMA, TASKS_PROJECT_STATUS_INDEX
from taskflow.api.seed import ensure_task_index

log = logging.getLogger("taskflow.faults")


@dataclass(frozen=True)
class FaultInfo:
    id: str
    wing: str
    title: str
    flag: str


CATALOG: dict[str, FaultInfo] = {
    "1": FaultInfo("1", "api", "N+1 query on GET /projects/{id}/tasks", "legacy_task_loader"),
    "3": FaultInfo(
        "3", "database", "Missing index on tasks(project_id, status)", "lazy_index_bootstrap"
    ),
    "4": FaultInfo(
        "4", "database", "Connection pool exhausted (leaked connections)", "legacy_conn_helper"
    ),
}


class FaultRegistry:
    def __init__(self, engine: Engine, admin_engine: Engine) -> None:
        self.engine = engine
        self.admin_engine = admin_engine
        self.leaked: list[Connection] = []  # connections the legacy helper never returned
        self._lock = threading.Lock()

    def is_on(self, fault_id: str) -> bool:
        return flags.enabled(CATALOG[fault_id].flag)

    def set(self, fault_id: str, on: bool) -> None:
        if fault_id not in CATALOG:
            raise KeyError(fault_id)
        with self._lock:
            if on == self.is_on(fault_id):
                return
            flags.set_flag(CATALOG[fault_id].flag, on)
            if fault_id == "3":
                if on:
                    with self.admin_engine.begin() as conn:
                        conn.execute(
                            text(f"DROP INDEX IF EXISTS {SCHEMA}.{TASKS_PROJECT_STATUS_INDEX}")
                        )
                else:
                    ensure_task_index(self.admin_engine)
            elif fault_id == "4" and not on:
                self.release_leaks()

    def release_leaks(self) -> None:
        leaked, self.leaked = self.leaked, []
        for conn in leaked:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - connection may already be gone
                log.debug("leaked connection already closed")

    def reset(self) -> None:
        for fid in CATALOG:
            self.set(fid, False)
        self.release_leaks()

    def status(self) -> list[dict[str, Any]]:
        return [
            {"id": f.id, "wing": f.wing, "title": f.title, "on": self.is_on(f.id)}
            for f in CATALOG.values()
        ]


def create_control_app(registry: FaultRegistry) -> FastAPI:
    app = FastAPI(title="TaskFlow fault control (simulation only)")

    @app.get("/faults")
    def list_faults() -> list[dict[str, Any]]:
        return registry.status()

    @app.post("/faults/reset")
    def reset() -> dict[str, str]:
        registry.reset()
        return {"status": "all faults off"}

    @app.post("/faults/{fault_id}/{action}")
    def toggle(fault_id: str, action: str) -> dict[str, Any]:
        if action not in {"on", "off"}:
            raise HTTPException(400, "action must be on or off")
        try:
            registry.set(fault_id, action == "on")
        except KeyError:
            raise HTTPException(404, f"unknown fault {fault_id}") from None
        return {"id": fault_id, "on": registry.is_on(fault_id)}

    return app
