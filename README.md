# OpsLoop

A multi-agent IT operations platform that watches a **simulated** SaaS app (TaskFlow),
finds the root cause of incidents with evidence, tests fixes in a sandbox, gets a human to
approve the exact tested version, releases it in stages with automatic rollback, and learns
from every outcome.

**Autonomous investigation and fix proposal, human-gated release.** Everything here is
simulated: the app, the traffic, the tenants and the faults.

- Live dashboard: <https://ops-loop.vercel.app> (connected to the backend while it runs)
- Run it and demo it: [docs/DEMO.md](docs/DEMO.md)
- Architecture: [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) · decisions: [docs/DECISIONS.md](docs/DECISIONS.md)

## The loop

1. **Watchers** (API, Database) detect anomalies from Prometheus metrics: thresholds, z-score, EWMA.
2. **Incident Manager** dedupes, correlates over the dependency graph, scores severity.
3. **Investigator** (LLM + hybrid RAG over runbooks and past incidents) writes Report 1 with
   cited evidence and a confidence; below 0.6 it escalates to a human.
4. **Coding agent** picks a strategy and a sector specialist writes the patch in a Docker sandbox.
5. **Test Loop** replays the fault, runs visible and hidden tests, security scan and acceptance
   criteria, and writes Report 2. The validated change gets a version hash.
6. **Human approval** of that exact hash (approve, modify and revalidate, or reject).
7. **Staged release**: candidate container behind a router, 5% → 25% → 100% with automatic
   rollback when its health falls below stable.
8. **Judge**: verdict from candidate-vs-control measurements with a bootstrap confidence interval.
9. **Learner**: RESOLVED fixes are added to the knowledge base.

Every step is written to a hash-chained, append-only **evidence ledger**.

## Quick start

Requires Docker Desktop, Python 3.12 and (for the public dashboard) cloudflared.

```bash
cp .env.example .env            # add a model key (GEMINI_API_KEY, or Anthropic/Groq)
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
```

Then double-click `Start-OpsLoop.cmd` (or open the folder in VS Code, which runs it). It starts
the stack, the worker and the public tunnel, and keeps them running. Local dashboard API:
<http://localhost:8000/health>.

Development:

```bash
pytest -v
ruff check . && ruff format --check . && mypy opsloop
cd web && npm install && npm run dev
```

## Stages

| Stage | Scope | Status |
|---|---|---|
| 0 | Repo, Compose, Postgres + pgvector, CI | done |
| 1 | TaskFlow, traffic, API + DB watchers, incidents, ledger, live issue board | done (detection gate measured in part) |
| 2 | RAG, Investigator, orchestrator, sandbox, Test Loop, approval | done, live with Gemini |
| 3 | Staged deployment, rollback, Judge | done, live: approval triggers the release |
| 4 | All six wings, decoys, Learner | partial: Learner adds resolved fixes to knowledge; four wings and threshold proposals not built |
| 5 | Evaluation, red-team, 7-day run | not started |
