# OpsLoop

A multi-agent IT operations platform that watches a **simulated** SaaS app (TaskFlow),
finds the root cause of incidents with evidence, tests fixes in a sandbox, gets a human to
approve the exact tested version, releases it in stages with automatic rollback, and learns
from every outcome.

**Autonomous investigation and fix proposal, human-gated release.** Everything here is
simulated: the app, the traffic, the tenants and the faults.

> Status: **Stage 0 (setup)**. No results exist yet. See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md)
> for the architecture and [docs/DECISIONS.md](docs/DECISIONS.md) for progress.

## Quick start

Requires Docker Desktop and Python 3.12.

```bash
cp .env.example .env
docker compose up -d --build --wait
curl http://localhost:8000/health
```

Development:

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate elsewhere
pip install -e ".[dev]"
pytest -v
ruff check . && ruff format --check . && mypy
```

## Stages

| Stage | Scope | Status |
|---|---|---|
| 0 | Repo, Compose, Postgres + pgvector, CI | in progress |
| 1 | TaskFlow, traffic, API + DB watchers, incidents, ledger, live issue board | not started |
| 2 | RAG, Investigator, orchestrator, sandbox, Test Loop, approval | not started |
| 3 | Staged deployment, rollback, Judge | not started |
| 4 | All six wings, decoys, Learner | not started |
| 5 | Evaluation, red-team, 7-day run | not started |
