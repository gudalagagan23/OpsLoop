"""Planted faults (simulated). Toggled only through the control app on a separate port that
OpsLoop agents can never reach. IDs follow the fault catalog in docs/PROJECT_SPEC.md §14."""

import logging
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from sqlalchemy import Connection, Engine, text

from taskflow.api.models import SCHEMA, TASKS_PROJECT_STATUS_INDEX
from taskflow.api.seed import ensure_task_index

log = logging.getLogger("taskflow.faults")


@dataclass(frozen=True)
class FaultInfo:
    id: str
    wing: str
    title: str


CATALOG: dict[str, FaultInfo] = {
    "1": FaultInfo("1", "api", "N+1 query on GET /projects/{id}/tasks"),
    "3": FaultInfo("3", "database", "Missing index on tasks(project_id, status)"),
    "4": FaultInfo("4", "database", "Connection pool exhausted (leaked connections)"),
}


class PoolLeak:
    """Fault 4: check out connections and never return them, one every `interval_s`."""

    def __init__(self, engine: Engine, interval_s: float) -> None:
        self.engine = engine
        self.interval_s = interval_s
        self.held: list[Connection] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pool-leak", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        size: int = self.engine.pool.size()  # type: ignore[attr-defined]
        while not self._stop.is_set() and len(self.held) < size:
            try:
                self.held.append(self.engine.connect())
            except Exception:  # noqa: BLE001 - pool busy; try again next tick
                log.debug("pool leak: no connection available yet")
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        for conn in self.held:
            conn.close()
        self.held.clear()


class FaultRegistry:
    def __init__(self, engine: Engine, admin_engine: Engine, pool_leak_interval_s: float) -> None:
        self.engine = engine
        self.admin_engine = admin_engine
        self._active: set[str] = set()
        self._leak = PoolLeak(engine, pool_leak_interval_s)
        self._lock = threading.Lock()

    def is_on(self, fault_id: str) -> bool:
        return fault_id in self._active

    def set(self, fault_id: str, on: bool) -> None:
        if fault_id not in CATALOG:
            raise KeyError(fault_id)
        with self._lock:
            if on == (fault_id in self._active):
                return
            if fault_id == "3":
                if on:
                    with self.admin_engine.begin() as conn:
                        conn.execute(
                            text(f"DROP INDEX IF EXISTS {SCHEMA}.{TASKS_PROJECT_STATUS_INDEX}")
                        )
                else:
                    ensure_task_index(self.admin_engine)
            elif fault_id == "4":
                self._leak.start() if on else self._leak.stop()
            (self._active.add if on else self._active.discard)(fault_id)

    def reset(self) -> None:
        for fid in list(self._active):
            self.set(fid, False)

    def status(self) -> list[dict[str, Any]]:
        return [
            {"id": f.id, "wing": f.wing, "title": f.title, "on": f.id in self._active}
            for f in CATALOG.values()
        ]


def create_control_app(registry: FaultRegistry) -> FastAPI:
    app = FastAPI(title="TaskFlow fault control (simulation only)")

    @app.get("/faults")
    def list_faults() -> list[dict[str, Any]]:
        return registry.status()

    @app.post("/faults/{fault_id}/{action}")
    def toggle(fault_id: str, action: str) -> dict[str, Any]:
        if action not in {"on", "off"}:
            raise HTTPException(400, "action must be on or off")
        try:
            registry.set(fault_id, action == "on")
        except KeyError:
            raise HTTPException(404, f"unknown fault {fault_id}") from None
        return {"id": fault_id, "on": registry.is_on(fault_id)}

    @app.post("/faults/reset")
    def reset() -> dict[str, str]:
        registry.reset()
        return {"status": "all faults off"}

    return app
