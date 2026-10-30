# TaskFlow architecture (simulated SaaS)

TaskFlow is a small multi-tenant project tracker. Everything about it is simulated.

## Services
`taskflow-api` (FastAPI, port 8080) serves `/auth/login`, `/projects`, `/projects/{id}/tasks`,
`/tasks/{id}`, `/health`, `/metrics`. It talks to PostgreSQL schema `taskflow` through a
SQLAlchemy pool of 10 connections with a 3 second checkout timeout. `taskflow-web` is the frontend.
The `payments-sim`, worker and frontend are not built yet in this stage.

## Dependency graph
`taskflow-api` depends on `postgres`. `taskflow-web` depends on `taskflow-api`.
A fault in `postgres` therefore has `taskflow-api` and `taskflow-web` in its blast radius.

## Code layout
`taskflow/api/main.py` holds the route handlers. `taskflow/api/seed.py` creates the schema and
the seed data. `taskflow/api/telemetry.py` defines the Prometheus metrics. Handlers wrap queries
in `query_name("...")` so `db_query_duration_seconds{query_name}` identifies each query.

## Metrics
`http_requests_total{route,method,status,plan}`, `http_request_duration_seconds{route,method}`,
`db_query_duration_seconds{query_name}`, `db_queries_per_request{route}`, `db_pool_in_use`,
`db_pool_size`, `db_pool_timeouts_total`, `app_health{component}`.

## Testing procedures
Visible tests live in `sandbox_tests/visible/` and run with `python -m pytest`. They need a
Postgres reachable through `TASKFLOW_DATABASE_URL`. Never edit existing tests.
