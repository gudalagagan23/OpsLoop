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
