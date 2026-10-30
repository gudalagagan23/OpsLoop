"""TaskFlow telemetry: Prometheus metrics (contract in spec section 4), per-request query
counting, and a structured-log sink that batches rows into ops.app_logs."""

import logging
import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import Engine, create_engine, event, text

log = logging.getLogger("taskflow")

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 2, 3, 5, 10)
_DB_BUCKETS = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5)

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["route", "method", "status", "plan"]
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP latency", ["route", "method"], buckets=_LATENCY_BUCKETS
)
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds", "DB query latency", ["query_name"], buckets=_DB_BUCKETS
)
DB_QUERIES_PER_REQUEST = Histogram(
    "db_queries_per_request",
    "DB queries issued while serving one request",
    ["route"],
    buckets=(1, 2, 3, 5, 10, 20, 50, 100, 200),
)
DB_POOL_IN_USE = Gauge("db_pool_in_use", "Checked-out DB connections")
DB_POOL_SIZE = Gauge("db_pool_size", "DB pool size")
DB_POOL_TIMEOUTS = Counter("db_pool_timeouts_total", "Requests that timed out waiting for the pool")
APP_HEALTH = Gauge("app_health", "1 if the component is healthy", ["component"])

_request_ctx: ContextVar[dict[str, Any] | None] = ContextVar("tf_request_ctx", default=None)
_query_name: ContextVar[str] = ContextVar("tf_query_name", default="other")


@contextmanager
def request_scope() -> Iterator[dict[str, Any]]:
    """Per-request mutable state; visible from threadpool workers (contexts are copied)."""
    ctx: dict[str, Any] = {"queries": 0, "plan": "anon", "tenant_id": None}
    token = _request_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _request_ctx.reset(token)


def current_request() -> dict[str, Any] | None:
    return _request_ctx.get()


@contextmanager
def query_name(name: str) -> Iterator[None]:
    token = _query_name.set(name)
    try:
        yield
    finally:
        _query_name.reset(token)


def instrument_engine(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, stmt: Any, params: Any, context: Any, many: Any) -> None:
        conn.info.setdefault("tf_start", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, stmt: Any, params: Any, context: Any, many: Any) -> None:
        started = conn.info["tf_start"].pop()
        DB_QUERY_DURATION.labels(_query_name.get()).observe(time.perf_counter() - started)
        ctx = _request_ctx.get()
        if ctx is not None:
            ctx["queries"] += 1

    DB_POOL_SIZE.set(engine.pool.size())  # type: ignore[attr-defined]
    DB_POOL_IN_USE.set_function(lambda: float(engine.pool.checkedout()))  # type: ignore[attr-defined]


class LogSink:
    """Structured JSON logs into ops.app_logs, batched on a background thread.

    Uses its own tiny engine so planted pool faults in the app pool don't block logging.
    """

    def __init__(self, url: str | None, *, service: str, version: str) -> None:
        self.service = service
        self.version = version
        self._q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=50_000)
        self._engine = create_engine(url, pool_size=1, max_overflow=0) if url else None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self._engine is not None:
            self._thread = threading.Thread(target=self._run, name="log-sink", daemon=True)
            self._thread.start()

    def emit(
        self,
        level: str,
        message: str,
        *,
        route: str | None = None,
        tenant_id: int | None = None,
        request_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "ts": datetime.now(UTC),
            "level": level,
            "service": self.service,
            "route": route,
            "tenant_id": tenant_id,
            "request_id": request_id,
            "version": self.version,
            "message": message,
            "attrs": attrs or {},
        }
        if self._engine is None:
            log.log(logging.getLevelName(level), "%s %s", message, attrs)
            return
        try:
            self._q.put_nowait(row)
        except queue.Full:
            pass  # dropping logs beats blocking requests

    def _run(self) -> None:
        import json

        if self._engine is None:
            return
        stmt = text(
            "INSERT INTO ops.app_logs "
            "(ts, level, service, route, tenant_id, request_id, version, message, attrs) "
            "VALUES (:ts, :level, :service, :route, :tenant_id, :request_id, :version, "
            ":message, CAST(:attrs AS jsonb))"
        )
        while not self._stop.is_set() or not self._q.empty():
            batch: list[dict[str, Any]] = []
            try:
                batch.append(self._q.get(timeout=1.0))
                while len(batch) < 500:
                    batch.append(self._q.get_nowait())
            except queue.Empty:
                pass
            if not batch:
                continue
            try:
                with self._engine.begin() as conn:
                    conn.execute(stmt, [{**r, "attrs": json.dumps(r["attrs"])} for r in batch])
            except Exception:  # noqa: BLE001 - the sink must never crash the app
                log.exception("log sink failed to write %d rows", len(batch))

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._engine is not None:
            self._engine.dispose()
