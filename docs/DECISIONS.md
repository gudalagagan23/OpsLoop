# Decisions log

## Stage 0: setup (2026-10-30)

### Open decisions from the spec (provisional, owner to confirm)

| Decision | Choice | Why |
|---|---|---|
| Outside coding agent | Claude Code, headless, behind `CodingAgentRunner` | Runs non-interactively, scoped tools; replaceable via the interface |
| LLM provider | Claude API behind `LLMClient`; model names in env only | Spec forbids hard-coded models |
| Embeddings | Local (sentence-transformers) behind `Embedder` | Free, offline, reproducible for seeded eval runs |
| Traffic split | Small FastAPI router | Easier to unit-test and to log stage changes than nginx |

### Other choices

- **Postgres driver:** psycopg 3 via SQLAlchemy 2 (`postgresql+psycopg://`).
- **Image:** `pgvector/pgvector:pg16`; the `vector` extension is created by an init script.
- **Config:** `pydantic-settings`, env prefix `OPSLOOP_`.
- **Host port 55432 for Postgres** (`POSTGRES_HOST_PORT`): the dev machine already runs native
  Postgres servers on 5432 and 5433, which silently took the connections and failed auth.
- **Owner request, 2026-10-30: live issue board.** One board for incidents from every wing,
  columns by lifecycle state, filters by wing/severity, and corner toast pop-ups
  (S1/S2 sticky, S3/S4 auto-dismiss) for new incidents, reports ready, approval needed,
  rollbacks and verdicts. Pulled into Stage 1 as part of the dashboard (component 12);
  approval actions arrive with Stage 2. Live updates via server-sent events.

### What works

- `docker compose up` starts Postgres (pgvector) and the OpsLoop API skeleton; `/health`
  reports database reachability and the pgvector extension.
- CI: ruff, mypy, unit + integration tests against a pgvector service, and a compose smoke test.

### Stubs

- None. Stage 0 contains only setup; all other components are not started.

## Stage 1: skeleton (2026-10-30)

Everything below runs against the simulated TaskFlow app with simulated traffic and planted faults.

### Choices

- **One database, two schemas.** `taskflow` holds the demo app's tables; `ops` holds the
  platform tables, including `app_logs` and `deploy_events`. OpsLoop tables use Alembic;
  TaskFlow (a demo app) uses `create_all` plus a deterministic SQL seed (20 tenants: 2
  enterprise, 6 pro, 12 free; 100 projects; 400k tasks).
- **Ledger hash covers an envelope, not just the payload.** Spec: `sha256(prev_hash ||
  canonical_json(payload))`. Implemented: the hashed JSON is `{incident_id, ts, type, source,
  payload}`, so metadata can't be altered without breaking the chain. One global chain,
  appends serialised with `pg_advisory_xact_lock`; a trigger blocks UPDATE/DELETE/TRUNCATE.
- **Metrics via `prometheus_client` directly**, not the OpenTelemetry SDK. Same metric names
  as the telemetry contract; OTel can wrap it later without changing watchers.
  Added two metrics beyond the contract: `db_queries_per_request{route}` (N+1 detection) and
  `db_pool_timeouts_total` (acceptance criterion for fault 4).
- **Logs:** errors, slow requests (≥500 ms) and a 2% sample of normal requests go to
  `ops.app_logs` through a batching sink with its own connection, so pool faults can't block it.
- **Watchers and the Incident Manager run in one engine process** with direct calls. The spec's
  Postgres job queue and LangGraph workflows are not needed yet (no long-running agent work);
  they arrive with Stage 2.
- **Correlation:** a new signal joins an active incident on the same or a directly connected
  service (dependency graph) whose last signal was within 5 minutes. Repeats of an open signal
  are deduplicated. Severity is a point score (signal, tenants, plan tier, error rate,
  duration, blast radius) with the breakdown written to the ledger; it only rises while active.
- **Simulated time:** one simulated day = 30 real minutes; a week = 3.5 hours.
- **Fault control:** `taskflow-api` serves faults on port 9100, bound to `127.0.0.1` on the host.
- **Thresholds come from a measured probe** (75 s per state, live stack, ~11 rps):

  | Metric | Baseline | Fault 1 (N+1) | Fault 3 (no index) | Fault 4 (pool leak) |
  |---|---|---|---|---|
  | p95 latency, task list | 36–43 ms | 2.63 s | 97 ms | 4.9 s |
  | p95 `tasks_by_project` query | 24 ms | 38 ms | 92 ms | no data |
  | p95 queries per request | 1.95 | 194 | 1.95 | 0.95 |
  | 5xx ratio | 0 | 0 | 0 | 1.0 |
  | pool in use / timeouts per s | 0 / 0 | 0 / 0 | 0 / 0 | 10 of 10 / 11.8 |

- **Gate interpretation:** "measured over at least 20 runs" is taken as 21 runs in total
  (7 seeded rounds × 3 faults). The harness seed fixes fault order and clean-window length;
  traffic uses its own fixed seed (42) and keeps running between runs.
- **Report 1 is generated as soon as an incident is triaged**, so it cites only the first
  signal's evidence. The Stage 2 Investigator should wait for correlated signals to settle.
