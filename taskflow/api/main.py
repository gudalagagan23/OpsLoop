"""TaskFlow API: a small multi-tenant project tracker (simulated SaaS monitored by OpsLoop)."""

import itertools
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.pool import NullPool

from taskflow import flags
from taskflow.api.models import SCHEMA
from taskflow.api.seed import hash_password, init_db
from taskflow.api.telemetry import (
    APP_HEALTH,
    DB_POOL_TIMEOUTS,
    DB_QUERIES_PER_REQUEST,
    HTTP_DURATION,
    HTTP_REQUESTS,
    LogSink,
    current_request,
    instrument_engine,
    query_name,
    request_scope,
)
from taskflow.config import TaskflowSettings, get_settings
from taskflow.faults.registry import FaultRegistry

S = SCHEMA
TASK_STATUSES = ("todo", "in_progress", "review", "done")


class LoginBody(BaseModel):
    email: str
    password: str


class NewTask(BaseModel):
    title: str
    description: str = ""


class TaskPatch(BaseModel):
    status: str


class Principal(BaseModel):
    user_id: int
    tenant_id: int
    plan: str


def _record_deploy(engine: Engine, settings: TaskflowSettings) -> None:
    """Record a deploy only when the running version changed: a plain restart is not a release,
    and counting restarts as deploys makes every incident look deploy-related."""
    try:
        with engine.begin() as conn:
            last = conn.execute(
                text(
                    "SELECT version, git_sha FROM ops.deploy_events "
                    "WHERE service = 'taskflow-api' ORDER BY id DESC LIMIT 1"
                )
            ).first()
            if last is not None and (last.version, last.git_sha) == (
                settings.version,
                settings.git_sha,
            ):
                return
            conn.execute(
                text(
                    "INSERT INTO ops.deploy_events (service, version, git_sha, stage, actor) "
                    "VALUES ('taskflow-api', :v, :sha, '100%', 'startup')"
                ),
                {"v": settings.version, "sha": settings.git_sha},
            )
    except ProgrammingError:
        pass  # no ops schema (e.g. an isolated sandbox database): nothing to record


def create_app(settings: TaskflowSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_engine(
        settings.database_url,
        pool_size=settings.pool_size,
        max_overflow=0,
        pool_timeout=settings.pool_timeout_s,
    )
    instrument_engine(engine)
    admin_engine = create_engine(settings.database_url, poolclass=NullPool)
    sink = LogSink(
        settings.database_url if settings.log_to_db else None,
        service="taskflow-api",
        version=settings.version,
    )
    registry = FaultRegistry(engine, admin_engine)
    request_counter = itertools.count(1)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(init_db, admin_engine, settings)
        await run_in_threadpool(_record_deploy, admin_engine, settings)
        APP_HEALTH.labels("api").set(1)
        yield
        registry.reset()
        sink.close()
        engine.dispose()
        admin_engine.dispose()

    app = FastAPI(title="TaskFlow (simulated)", lifespan=lifespan)
    app.state.engine = engine
    app.state.registry = registry
    app.state.sink = sink
    app.state.settings = settings

    # ---- dependencies ---------------------------------------------------------------------

    def get_conn() -> Iterator[Connection]:
        if flags.enabled("legacy_conn_helper"):
            # Legacy helper: every 4th request keeps its connection and never returns it.
            conn = engine.connect()
            if next(request_counter) % 4 == 0:
                registry.leaked.append(conn)
                yield conn
                return
            try:
                yield conn
            finally:
                conn.close()
            return
        with engine.connect() as conn:
            yield conn

    def principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            claims = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(401, "invalid token") from None
        p = Principal(user_id=int(claims["sub"]), tenant_id=claims["tid"], plan=claims["plan"])
        ctx = current_request()
        if ctx is not None:
            ctx["plan"], ctx["tenant_id"] = p.plan, p.tenant_id
        return p

    Conn = Annotated[Connection, Depends(get_conn)]
    Me = Annotated[Principal, Depends(principal)]

    # ---- middleware and errors ------------------------------------------------------------

    @app.middleware("http")
    async def telemetry(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        with request_scope() as ctx:
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
            finally:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                elapsed = time.perf_counter() - start
                HTTP_REQUESTS.labels(route, request.method, str(status), ctx["plan"]).inc()
                HTTP_DURATION.labels(route, request.method).observe(elapsed)
                DB_QUERIES_PER_REQUEST.labels(route).observe(ctx["queries"])
                ms = elapsed * 1000
                level = (
                    "ERROR"
                    if status >= 500
                    else "WARNING"
                    if ms >= settings.slow_request_ms
                    else "INFO"
                )
                if level != "INFO" or random.random() < settings.info_log_sample:  # noqa: S311
                    sink.emit(
                        level,
                        f"{request.method} {route} {status} {ms:.0f}ms",
                        route=route,
                        tenant_id=ctx["tenant_id"],
                        request_id=rid,
                        attrs={
                            "method": request.method,
                            "status": status,
                            "duration_ms": round(ms, 1),
                            "plan": ctx["plan"],
                            "db_queries": ctx["queries"],
                        },
                    )
        response.headers["x-request-id"] = rid
        return response

    @app.exception_handler(PoolTimeoutError)
    async def pool_timeout(request: Request, exc: PoolTimeoutError) -> JSONResponse:
        DB_POOL_TIMEOUTS.inc()
        ctx = current_request() or {}
        sink.emit(
            "ERROR",
            "database connection pool timeout (QueuePool limit reached)",
            route=getattr(request.scope.get("route"), "path", None),
            tenant_id=ctx.get("tenant_id"),
            attrs={"error": "pool_timeout", "plan": ctx.get("plan", "anon")},
        )
        return JSONResponse({"detail": "service temporarily unavailable"}, status_code=503)

    # ---- routes ---------------------------------------------------------------------------

    @app.get("/health")
    def health(response: Response) -> dict[str, str]:
        try:
            with engine.connect() as conn, query_name("health_check"):
                conn.execute(text("SELECT 1"))
            APP_HEALTH.labels("db").set(1)
            return {"status": "ok"}
        except PoolTimeoutError:
            APP_HEALTH.labels("db").set(0)
            response.status_code = 503
            return {"status": "degraded"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/auth/login")
    def login(body: LoginBody, conn: Conn) -> dict[str, str]:
        with query_name("auth_user_by_email"):
            row = conn.execute(
                text(
                    f"SELECT u.id, u.tenant_id, u.password_hash, t.plan FROM {S}.users u "
                    f"JOIN {S}.tenants t ON t.id = u.tenant_id WHERE u.email = :e"
                ),
                {"e": body.email},
            ).first()
        if row is None or row.password_hash != hash_password(body.password):
            raise HTTPException(401, "invalid credentials")
        ctx = current_request()
        if ctx is not None:
            ctx["plan"], ctx["tenant_id"] = row.plan, row.tenant_id
        token = jwt.encode(
            {
                "sub": str(row.id),
                "tid": row.tenant_id,
                "plan": row.plan,
                "exp": datetime.now(UTC) + timedelta(seconds=settings.jwt_ttl_s),
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        return {"access_token": token, "token_type": "bearer"}

    @app.get("/projects")
    def list_projects(me: Me, conn: Conn) -> list[dict[str, Any]]:
        with query_name("projects_by_tenant"):
            rows = conn.execute(
                text(f"SELECT id, name FROM {S}.projects WHERE tenant_id = :t ORDER BY id"),
                {"t": me.tenant_id},
            ).mappings()
            return [dict(r) for r in rows]

    def _own_project(conn: Connection, me: Principal, project_id: int) -> None:
        with query_name("project_by_id"):
            ok = conn.execute(
                text(f"SELECT 1 FROM {S}.projects WHERE id = :p AND tenant_id = :t"),
                {"p": project_id, "t": me.tenant_id},
            ).first()
        if ok is None:
            raise HTTPException(404, "project not found")

    @app.get("/projects/{project_id}/tasks")
    def list_tasks(
        project_id: int, me: Me, conn: Conn, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        _own_project(conn, me, project_id)
        limit = max(1, min(limit, 100))
        where = "t.project_id = :p" + (" AND t.status = :s" if status else "")
        params: dict[str, Any] = {"p": project_id, "s": status, "n": limit}
        name = "tasks_by_project_status" if status else "tasks_by_project"
        if flags.enabled("legacy_task_loader"):
            # Legacy loader: one query for the tasks, then extra queries per row.
            with query_name(name):
                tasks = [
                    dict(r)
                    for r in conn.execute(
                        text(
                            f"SELECT t.id, t.title, t.status, t.created_at, t.assignee_id "
                            f"FROM {S}.tasks t WHERE {where} "
                            "ORDER BY t.created_at DESC LIMIT :n"
                        ),
                        params,
                    ).mappings()
                ]
            for t in tasks:
                with query_name("task_assignee"):
                    t["assignee"] = conn.execute(
                        text(f"SELECT name FROM {S}.users WHERE id = :u"), {"u": t["assignee_id"]}
                    ).scalar()
                with query_name("task_comment_count"):
                    t["comment_count"] = conn.execute(
                        text(f"SELECT count(*) FROM {S}.comments WHERE task_id = :i"),
                        {"i": t["id"]},
                    ).scalar_one()
                del t["assignee_id"]
            return tasks
        with query_name(name):
            rows = conn.execute(
                text(
                    "SELECT t.id, t.title, t.status, t.created_at, u.name AS assignee, "
                    f"(SELECT count(*) FROM {S}.comments c WHERE c.task_id = t.id) "
                    "AS comment_count "
                    f"FROM {S}.tasks t LEFT JOIN {S}.users u ON u.id = t.assignee_id "
                    f"WHERE {where} ORDER BY t.created_at DESC LIMIT :n"
                ),
                params,
            ).mappings()
            return [dict(r) for r in rows]

    @app.post("/projects/{project_id}/tasks", status_code=201)
    def create_task(project_id: int, body: NewTask, me: Me, conn: Conn) -> dict[str, Any]:
        _own_project(conn, me, project_id)
        with query_name("task_insert"):
            task_id = conn.execute(
                text(
                    f"INSERT INTO {S}.tasks (project_id, tenant_id, title, description, "
                    "status, assignee_id) VALUES (:p, :t, :title, :d, 'todo', :u) RETURNING id"
                ),
                {
                    "p": project_id,
                    "t": me.tenant_id,
                    "title": body.title[:256],
                    "d": body.description,
                    "u": me.user_id,
                },
            ).scalar_one()
        conn.commit()
        return {"id": task_id, "status": "todo"}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: int, me: Me, conn: Conn) -> dict[str, Any]:
        with query_name("task_by_id"):
            row = (
                conn.execute(
                    text(
                        f"SELECT id, project_id, title, description, status, created_at "
                        f"FROM {S}.tasks WHERE id = :i AND tenant_id = :t"
                    ),
                    {"i": task_id, "t": me.tenant_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(404, "task not found")
        with query_name("comments_by_task"):
            comments = conn.execute(
                text(
                    f"SELECT id, user_id, body, created_at FROM {S}.comments "
                    "WHERE task_id = :i ORDER BY id"
                ),
                {"i": task_id},
            ).mappings()
            return {**dict(row), "comments": [dict(c) for c in comments]}

    @app.patch("/tasks/{task_id}")
    def update_task(task_id: int, body: TaskPatch, me: Me, conn: Conn) -> dict[str, Any]:
        if body.status not in TASK_STATUSES:
            raise HTTPException(422, f"status must be one of {TASK_STATUSES}")
        with query_name("task_update_status"):
            updated = conn.execute(
                text(
                    f"UPDATE {S}.tasks SET status = :s, updated_at = now() "
                    "WHERE id = :i AND tenant_id = :t RETURNING id"
                ),
                {"s": body.status, "i": task_id, "t": me.tenant_id},
            ).scalar()
        conn.commit()
        if updated is None:
            raise HTTPException(404, "task not found")
        return {"id": task_id, "status": body.status}

    return app
