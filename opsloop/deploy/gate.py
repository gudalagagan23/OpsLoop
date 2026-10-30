"""Stage 3 gate: live staged rollouts against the running stack.

    python -m opsloop.deploy.gate

Two rollouts through a real router, a real candidate container and the real TaskFlow database:

  1. BAD patch (POST /tasks returns 500). Must roll back automatically, with traffic moved off
     the candidate and the candidate container removed.
  2. GOOD fix for planted fault 1 (N+1 loader removed), with fault 1 armed on stable. Must
     complete 5% -> 25% -> 100%, then be judged by the Judge on the measured requests.

Traffic is driven through the router only, so every measurement is the candidate's own.
Requires: docker compose up (stack running), fault control on 127.0.0.1:9100.
"""

import json
import logging
import random
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from opsloop.agents.judge import Target, judge
from opsloop.deploy.candidate import DockerCandidateBackend
from opsloop.deploy.clients import HttpRouter
from opsloop.deploy.rollout import Rollout, RolloutConfig, RolloutResult
from opsloop.deploy.router import Split, create_router_app
from opsloop.testloop import reference_fixes as rf
from opsloop.testloop.patchutil import make_diff

log = logging.getLogger("opsloop.gate")

STABLE = "http://localhost:8080"
CONTROL = "http://localhost:9100"
ROUTER_PORT = 8090
RESULTS = Path("eval/results")
CREATE_BAD_PATCH_TARGET = "taskflow/api/main.py"


def bad_patch() -> str:
    src = (Path(__file__).resolve().parents[2] / CREATE_BAD_PATCH_TARGET).read_text(
        encoding="utf-8"
    )
    signature = "    def create_task(project_id: int, body: NewTask, me: Me, conn: Conn) -> dict[str, Any]:\n"
    if src.count(signature) != 1:
        raise RuntimeError("create_task signature moved; update the bad patch")
    injected = '        raise HTTPException(500, "bad release: task creation broken")\n'
    broken = src.replace(signature, signature + injected)
    return make_diff(CREATE_BAD_PATCH_TARGET, src, broken)


class Traffic:
    """Steady user traffic through the router: mostly task lists, one create in six."""

    def __init__(self, token: str, rps: float = 12.0) -> None:
        self.headers = {"Authorization": f"Bearer {token}"}
        self.rps = rps
        self.stop = threading.Event()
        self.sent = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        rng = random.Random(7)  # noqa: S311 - load shape, not security
        with httpx.Client(base_url=f"http://127.0.0.1:{ROUTER_PORT}", timeout=10) as c:
            try:
                project = c.get("/projects", headers=self.headers).json()[0]["id"]
            except (httpx.HTTPError, IndexError, KeyError):
                log.exception("cannot read projects through the router")
                return
            while not self.stop.is_set():
                rid = uuid.uuid4().hex
                try:
                    if rng.random() < 1 / 6:
                        c.post(
                            f"/projects/{project}/tasks",
                            json={"title": "gate"},
                            headers={**self.headers, "x-request-id": rid},
                        )
                    else:
                        c.get(
                            f"/projects/{project}/tasks",
                            headers={**self.headers, "x-request-id": rid},
                        )
                except httpx.HTTPError:
                    pass
                self.sent += 1
                self.stop.wait(1 / self.rps)

    def start(self) -> None:
        self.thread.start()

    def halt(self) -> None:
        self.stop.set()
        self.thread.join(timeout=10)


def start_router(split: Split) -> uvicorn.Server:
    app = create_router_app(STABLE, "http://localhost:8081", split)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=ROUTER_PORT, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("router did not start")
    return server


def login() -> str:
    r = httpx.post(
        f"{STABLE}/auth/login",
        json={"email": "user1@tenant1.sim", "password": "taskflow-sim-password"},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def set_fault(fault: str, on: bool) -> None:
    httpx.post(f"{CONTROL}/faults/{fault}/{'on' if on else 'off'}", timeout=10).raise_for_status()


def open_api_signals() -> int:
    from sqlalchemy import func, select

    from opsloop.core.db import get_engine
    from opsloop.core.models import Signal

    with get_engine().connect() as conn:
        return int(
            conn.execute(
                select(func.count())
                .select_from(Signal)
                .where(Signal.cleared_at.is_(None), Signal.watcher == "api")
            ).scalar_one()
        )


def run_rollout(
    name: str,
    patch: str,
    version: str,
    *,
    flags: list[str],
    soak_s: float,
    split: Split,
    server_ready: bool = True,
) -> dict[str, Any]:
    backend = DockerCandidateBackend(flags=flags)
    router = HttpRouter(f"http://127.0.0.1:{ROUTER_PORT}")
    events: list[dict[str, Any]] = []

    def on_event(kind: str, pct: int, data: dict[str, Any]) -> None:
        events.append({"t": time.time(), "event": kind, "pct": pct, **data})
        log.info("[%s] %s at %d%% %s", name, kind, pct, data)

    class LiveBackend:
        def start_candidate(self, patch: str, version_hash: str, fl: list[str]) -> str:
            name_ = backend.start_candidate(patch, version_hash, fl)
            if not backend.wait_healthy():
                raise RuntimeError("candidate container never became healthy")
            return name_

        candidate_healthy = backend.candidate_healthy
        stable_healthy = backend.stable_healthy
        stop_candidate = backend.stop_candidate

    cfg = RolloutConfig(stages=(5, 25, 100), soak_s=soak_s, poll_s=2.0, start_timeout_s=150)
    roll = Rollout(router, LiveBackend(), cfg, on_event=on_event)
    started = time.time()
    try:
        result: RolloutResult = roll.run(patch, version, flags)
    finally:
        split.set(0)
        backend.stop_candidate()
    out: dict[str, Any] = {
        "name": name,
        "outcome": result.outcome,
        "stage_reached": result.stage_pct,
        "reasons": result.reasons,
        "rollback_seconds": result.rollback_seconds,
        "wall_seconds": round(time.time() - started, 1),
        "events": events,
        "stages": [
            {
                "pct": s.pct,
                "candidate": asdict(s.candidate) if s.candidate else None,
                "control": asdict(s.control) if s.control else None,
            }
            for s in result.stages
        ],
        "_stage_samples": {s.pct: s.samples for s in result.stages},
    }
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    RESULTS.mkdir(parents=True, exist_ok=True)
    split = Split()
    server = start_router(split)
    token = login()
    traffic = Traffic(token)
    traffic.start()
    report: dict[str, Any] = {"simulated": True, "stage": 3, "started_at": time.time()}
    try:
        time.sleep(15)  # let steady traffic establish a control baseline first
        bad = run_rollout(
            "bad_release", bad_patch(), "bad" * 21 + "x", flags=[], soak_s=60, split=split
        )
        report["bad_release"] = _public(bad)

        set_fault("1", True)
        time.sleep(20)
        good_patch = rf.fix_fault1()
        good = run_rollout("fault1_fix", good_patch, "good" * 16, flags=[], soak_s=75, split=split)
        set_fault("1", False)
        report["fault1_fix"] = _public(good)

        if good["outcome"] == "completed":
            measured = sum(len(v) for v in good["_stage_samples"].values())
            verdict = judge(
                target=Target("list_p95_ms", "<=", 300),
                samples_by_stage=good["_stage_samples"],
                rolled_back=False,
                signals_cleared=open_api_signals() == 0,
            )
            report["fault1_judgement"] = {
                "verdict": verdict.verdict,
                "reasons": verdict.reasons,
                "effects": verdict.effects,
                "gap_closed": verdict.gap_closed,
                "candidate_samples": verdict.candidate_samples,
                "measured_requests": measured,
            }
    finally:
        traffic.halt()
        server.should_exit = True
        set_fault("1", False)
        report["requests_sent_through_router"] = traffic.sent
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = RESULTS / f"stage3_gate_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("bad_release", "fault1_fix")},
            indent=2,
            default=str,
        )
    )
    print(
        f"bad release: {report['bad_release']['outcome']} at {report['bad_release']['stage_reached']}% "
        f"after {report['bad_release']['wall_seconds']}s; reasons: {report['bad_release']['reasons']}"
    )
    print(
        f"fault 1 fix: {report['fault1_fix']['outcome']} to {report['fault1_fix']['stage_reached']}%"
    )
    print("results:", path)


def _public(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


if __name__ == "__main__":
    main()
