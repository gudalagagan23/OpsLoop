"""Traffic router for staged rollout: splits proxied requests between the stable and candidate
TaskFlow instances and records what each one returned (the controller's measurement source).

The split is deterministic per request id (a hash), so one user's requests stay on one side
during a stage. The admin endpoints are for the rollout controller only and must not be
reachable from agents (bind them to the internal network, like the fault control port)."""

import hashlib
import threading
import time
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from opsloop.deploy.health import Sample


class Split:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pct = 0
        self._samples: list[Sample] = []

    @property
    def pct(self) -> int:
        return self._pct

    def set(self, pct: int) -> None:
        if not 0 <= pct <= 100:
            raise ValueError("split must be 0..100")
        with self._lock:
            self._pct = pct

    def choose(self, request_id: str) -> str:
        bucket = int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 100
        return "candidate" if bucket < self._pct else "stable"

    def record(self, sample: Sample) -> None:
        with self._lock:
            self._samples.append(sample)

    def since(self, ts: float) -> list[Sample]:
        with self._lock:
            return [s for s in self._samples if s.ts >= ts]

    def trim(self, before: float) -> None:
        with self._lock:
            self._samples = [s for s in self._samples if s.ts >= before]


def classify(method: str, path: str) -> str:
    if method == "POST" and path.endswith("/tasks"):
        return "create"
    if method == "GET" and "/tasks" in path:
        return "list"
    return "other"


def create_router_app(
    stable_url: str,
    candidate_url: str | None,
    split: Split | None = None,
    upstream: httpx.AsyncClient | None = None,
) -> FastAPI:
    split = split or Split()
    upstreams = {"stable": stable_url, "candidate": candidate_url}
    client = upstream or httpx.AsyncClient(timeout=10.0)
    app = FastAPI(title="OpsLoop traffic router (staged rollout)")
    app.state.split = split

    @app.get("/_router/status")
    def status() -> dict[str, Any]:
        return {"candidate_pct": split.pct, "has_candidate": candidate_url is not None}

    @app.post("/_router/split/{pct}")
    def set_split(pct: int) -> dict[str, int]:
        if not 0 <= pct <= 100:
            raise HTTPException(422, "split must be between 0 and 100")
        if candidate_url is None and pct > 0:
            raise HTTPException(409, "no candidate is running")
        split.set(pct)
        return {"candidate_pct": split.pct}

    @app.get("/_router/samples")
    def samples(since: float = 0.0) -> list[dict[str, Any]]:
        return [asdict(s) for s in split.since(since)]

    # Registered last: the catch-all must not shadow the admin routes above.
    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    async def proxy(path: str, request: Request) -> Response:
        if path.startswith("_router/"):
            raise HTTPException(404)
        rid = request.headers.get("x-request-id") or str(time.time_ns())
        target = split.choose(rid)
        url = upstreams.get(target)
        if url is None:  # no candidate running: fall back to stable, record honestly
            target = "stable"
            url = stable_url
        started = time.time()
        body = await request.body()
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}
        }
        headers["x-request-id"] = rid
        try:
            upstream = await client.request(
                request.method,
                f"{url}/{path}",
                params=request.query_params,
                content=body,
                headers=headers,
            )
            status, content, out_headers = upstream.status_code, upstream.content, upstream.headers
        except httpx.HTTPError:
            status, content, out_headers = (
                502,
                b'{"detail":"upstream unavailable"}',
                httpx.Headers(),
            )
        ms = (time.time() - started) * 1000
        split.record(Sample(started, target, status, ms, classify(request.method, f"/{path}")))
        media = out_headers.get("content-type", "application/json")
        return Response(content=content, status_code=status, media_type=media)

    return app
