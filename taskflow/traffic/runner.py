"""Drive simulated user traffic against TaskFlow following a seeded TrafficModel."""

import asyncio
import logging
import os
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from taskflow.api.seed import SIM_PASSWORD
from taskflow.traffic.model import Event, Profile, TrafficModel

log = logging.getLogger("traffic")
STATUSES = ("todo", "in_progress", "review", "done")


@dataclass
class TenantSession:
    tenant_id: int
    plan: str
    headers: dict[str, str]
    projects: list[int]
    recent_tasks: deque[int] = field(default_factory=lambda: deque(maxlen=200))


class TrafficRunner:
    def __init__(
        self, base_url: str, profile: Profile, seed: int, tenants: int, concurrency: int = 64
    ) -> None:
        self.base_url = base_url
        self.profile = profile
        self.seed = seed
        self.n_tenants = tenants
        self.sem = asyncio.Semaphore(concurrency)
        self.stats: Counter[str] = Counter()
        self.sessions: dict[int, TenantSession] = {}

    async def _login(self, client: httpx.AsyncClient, tenant: int) -> TenantSession:
        r = await client.post(
            "/auth/login",
            json={"email": f"user1@tenant{tenant}.sim", "password": SIM_PASSWORD},
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        headers = {"Authorization": f"Bearer {token}"}
        projects = [p["id"] for p in (await client.get("/projects", headers=headers)).json()]
        return TenantSession(tenant, claims["plan"], headers, projects)

    async def _setup(self, client: httpx.AsyncClient) -> None:
        while True:
            try:
                for t in range(1, self.n_tenants + 1):
                    self.sessions[t] = await self._login(client, t)
                return
            except httpx.HTTPError as exc:
                log.warning("TaskFlow not ready (%s); retrying", exc)
                await asyncio.sleep(3)

    async def _do(self, client: httpx.AsyncClient, ev: Event) -> None:
        s = self.sessions[ev.tenant_id]
        project = s.projects[int(ev.choice * len(s.projects))]
        action = ev.action
        if action in {"get_task", "update_task"} and not s.recent_tasks:
            action = "list_tasks"
        try:
            async with self.sem:
                if action == "list_projects":
                    r = await client.get("/projects", headers=s.headers)
                elif action in {"list_tasks", "list_tasks_status"}:
                    params: dict[str, Any] = {}
                    if action == "list_tasks_status":
                        params["status"] = STATUSES[int(ev.choice * 997) % 4]
                    r = await client.get(
                        f"/projects/{project}/tasks", headers=s.headers, params=params
                    )
                    if r.status_code == 200:
                        s.recent_tasks.extend(t["id"] for t in r.json()[:5])
                elif action == "get_task":
                    task = s.recent_tasks[int(ev.choice * len(s.recent_tasks))]
                    r = await client.get(f"/tasks/{task}", headers=s.headers)
                elif action == "create_task":
                    r = await client.post(
                        f"/projects/{project}/tasks",
                        headers=s.headers,
                        json={"title": "Simulated task"},
                    )
                else:
                    task = s.recent_tasks[int(ev.choice * len(s.recent_tasks))]
                    r = await client.patch(
                        f"/tasks/{task}",
                        headers=s.headers,
                        json={"status": STATUSES[int(ev.choice * 991) % 4]},
                    )
            if r.status_code == 401:
                # Tokens expire (default one hour). Log in again so traffic never silently stops.
                self.sessions[ev.tenant_id] = await self._login(client, ev.tenant_id)
                self.stats["token_renewed"] += 1
            self.stats[f"{r.status_code // 100}xx"] += 1
        except httpx.HTTPError:
            self.stats["transport_error"] += 1

    async def run(self, duration_s: float | None = None) -> Counter[str]:
        limits = httpx.Limits(max_connections=128, max_keepalive_connections=64)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10, limits=limits) as client:
            await self._setup(client)
            tenants = [(t, s.plan) for t, s in self.sessions.items()]
            model = TrafficModel(self.profile, self.seed, tenants)
            start = time.monotonic()
            window = 0.25
            sim_t = 0.0
            last_report = 0.0
            tasks: set[asyncio.Task[None]] = set()
            while duration_s is None or sim_t < duration_s:
                for ev in model.events(sim_t, sim_t + window):
                    delay = start + ev.t - time.monotonic()
                    task = asyncio.create_task(self._delayed(client, ev, delay))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                sim_t += window
                await asyncio.sleep(max(0.0, start + sim_t - time.monotonic()))
                if sim_t - last_report >= 30:
                    last_report = sim_t
                    log.info("t=%.0fs stats=%s", sim_t, dict(self.stats))
            if tasks:
                await asyncio.gather(*tasks)
        return self.stats

    async def _delayed(self, client: httpx.AsyncClient, ev: Event, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await self._do(client, ev)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    profile_path = os.environ.get("TRAFFIC_PROFILE")
    profile = Profile.load(profile_path) if profile_path else Profile()
    runner = TrafficRunner(
        base_url=os.environ.get("TASKFLOW_URL", "http://localhost:8080"),
        profile=profile,
        seed=int(os.environ.get("TRAFFIC_SEED", "42")),
        tenants=int(os.environ.get("TRAFFIC_TENANTS", "20")),
    )
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
