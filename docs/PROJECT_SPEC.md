# OpsLoop: Multi-Agent Autonomous IT Operations Platform

**Status:** final architecture, ready to build. Nothing is implemented yet.
**Owner:** Gudala Gagan
**Setting:** a simulated multi-tenant SaaS product. All data, traffic, contracts and faults are synthetic.

## 1. Summary

OpsLoop watches a running SaaS application around the clock, detects operational problems, finds their root cause with evidence, proposes and validates a fix in an isolated sandbox, asks a human to approve the exact validated version, releases it in stages with automatic rollback, judges the real outcome, and learns from every case.

It is autonomous up to the human approval gate. Describe it that way everywhere ("autonomous investigation and fix proposal, human-gated release"). Never call it "fully autonomous".

### The rule that defines the project

An incident is resolved only when (1) the required validation succeeds, (2) the staged deployment meets its acceptance criteria, and (3) the Judge records the outcome with measurable evidence. An agent generating a fix is never enough.

### Goals

- Continuously monitor six wings of a SaaS platform: API, Database, Auth, Billing, Infrastructure, User Experience.
- Turn raw signals into correlated incidents with an auditable chain of evidence.
- Produce an evidence-backed investigation report (Report 1) and a validation report (Report 2).
- Fix a defined class of incidents with sector-specialised coding, validated in a sandbox.
- Keep a human in control of every release, bound to the exact validated version.
- Measure everything, so results can be reported honestly.

### Non-goals (do not build these)

- Training or fine-tuning any model.
- Anything that touches real production systems, real customer data or real money.
- Support for arbitrary systems. The claim is limited to systems that export standard telemetry and allow controlled rollouts.
- Fully autonomous release without human approval.
- Loki/ELK, Jaeger, Kafka/RabbitMQ and Kubernetes in v1 (see section 17).

## 2. Principles

- **Evidence first.** Every claim in a report must cite evidence IDs from the Evidence Ledger.
- **Plain code where possible.** Only investigation, fix writing, judging and learning use AI. Detection rules, the Test Loop, deployment, policy checks and state machines are deterministic code.
- **Deny by default.** Agents get the minimum permissions. Policy checks run in code outside any agent process.
- **Data, not instructions.** Text from logs, tickets, documents and code comments is never treated as instructions.
- **Bounded effort.** Every loop has retry, time and cost budgets, and escalates when exhausted.
- **Reproducible.** Seeded randomness, versioned configs, deterministic replay.
- **Honest scope.** Everything is labelled simulated. Stubs are labelled `# STUB`.

## 3. Architecture: the 17 components

| # | Component | Job | Built as |
|---|---|---|---|
| 1 | SaaS app (TaskFlow) | The monitored system | Plain code |
| 2 | Telemetry layer | Metrics, structured logs, deploy events (traces deferred) | OpenTelemetry SDK, Prometheus, Postgres log table |
| 3 | Watcher registry + watchers | Six core watchers from one codebase, configured per wing | Rules + statistics; LLM only when needed |
| 4 | Incident Manager | Dedupe, correlate, severity, lifecycle | Plain code |
| 5 | Evidence Ledger | Append-only, hash-chained record of everything | PostgreSQL |
| 6 | Knowledge and RAG layer | Runbooks, known incidents, docs, past fixes | pgvector + full-text hybrid search |
| 7 | Investigator agent | Root cause + Report 1 | Own agent (LLM + RAG) |
| 8 | Main Coding Agent (orchestrator) | Picks strategy, delegates, tracks attempts, retries/switches/escalates | Own orchestration + one outside coding agent |
| 9 | Sector specialists | API, Database, Auth and Security, Billing, Infrastructure, User Experience | Profiles on the coding agent |
| 10 | Sandbox | Isolated execution of proposed changes | Docker |
| 11 | Test Loop | Reproduce, test, compare, Report 2 | Plain code |
| 12 | Live dashboard | Live data, issue board, Report 1, Report 2 | Next.js + FastAPI |
| 13 | Human approval | Approve / modify / reject, bound to version hash | Next.js + backend |
| 14 | Deployment controller | Staged release, health checks, rollback | Plain Python |
| 15 | Judge agent | Outcome verdict from measured evidence | Own agent + statistics |
| 16 | Learner agent | Versioned improvements, backtested | Own agent + replay |
| 17 | Feedback to watchers | Approved updates go live, reversible | Versioned configs |

### Supporting components (keep each small)

- **Dependency graph:** a table of service relationships, used for blast radius and investigation context.
- **Incident replay engine:** deterministic scenarios (fault toggles + traffic profile + seed) for testing, validation and evaluation.
- **Uncertainty and escalation rules:** confidence thresholds that route to a human.
- **Policy and permission engine:** YAML policies enforced in code.
- **Event and workflow management:** LangGraph state machines with a PostgreSQL checkpointer, a PostgreSQL job queue (`FOR UPDATE SKIP LOCKED`), idempotent handlers, retries with backoff, a dead-letter table.

### End-to-end flow

```
TaskFlow -> telemetry -> Watchers -> Incident Manager <-> Evidence Ledger
   -> Investigator (+RAG) -> Report 1 -> Dashboard
   -> Main Coding Agent -> Specialist -> patch
   -> Sandbox + Test Loop -> Report 2 -> Human approval (pop-up)
   -> Deployment controller (5% -> 25% -> 100%, auto-rollback)
   -> Judge -> outcome -> Learner -> proposals -> backtest -> approval -> Watchers
```

Failed validation returns to the orchestrator for a bounded retry, a change of specialist, more investigation, or human escalation.

## 4. The demo SaaS app: TaskFlow

A small multi-tenant project-tracking SaaS with one real part per monitored wing.

| Wing | TaskFlow contents |
|---|---|
| API | FastAPI routes: `/auth/*`, `/projects`, `/projects/{id}/tasks`, `/tasks/{id}`, `/billing/*`, `/health` |
| Database | PostgreSQL; tables tenants, users, projects, tasks, comments, subscriptions, invoices; realistic indexes |
| Auth | Login, JWT access tokens, per-tenant users, plans free / pro / enterprise |
| Billing | payments-sim (a fake payment provider: configurable latency/failure, webhooks) |
| Infra | A background worker consuming a Postgres job queue (email digests, exports, invoice jobs) |
| User Experience | Minimal Next.js frontend that reports client errors to `/telemetry/client-errors` |

### Services (docker-compose)

postgres (with pgvector), taskflow-api, taskflow-worker, payments-sim, taskflow-web, otel-collector (optional), prometheus, traffic-gen, router (traffic splitter for staged release), plus the OpsLoop services.

### Traffic generator

- Python asyncio + httpx, seeded, with day/night and weekday/weekend cycles and per-tenant behaviour.
- Scenario files define profile, duration and fault toggles.
- A "break it" switch per fault: `POST /faults/{id}/on|off` (only on the sandbox/sim control port, never reachable by agents).

### Telemetry contract

Metrics (Prometheus):

```
http_requests_total{route,method,status,plan}
http_request_duration_seconds{route,method}            # histogram
db_query_duration_seconds{query_name}                  # histogram
db_pool_in_use, db_pool_size, db_lock_wait_seconds
auth_login_total{result}, auth_token_errors_total{reason}
billing_payment_total{result}, billing_webhook_total{result}
queue_depth{queue}, job_duration_seconds{job}, worker_cpu_ratio
frontend_errors_total{page}
app_health{component}
```

Structured logs (JSON), stored in table `app_logs`: ts, level, service, route, tenant_id, request_id, version, message, attrs.

Deploy events, table `deploy_events`: ts, service, version, git_sha, stage, actor.

## 5. Watchers

One Watcher class, six YAML configurations, versioned in table `watcher_configs`.

```yaml
watcher: api
version: 3
signals:
  - id: api.error_rate
    query: 'sum(rate(http_requests_total{status=~"5.."}[2m])) / sum(rate(http_requests_total[2m]))'
    method: threshold            # threshold | zscore | ewma | seasonal_baseline
    params: {warn: 0.02, crit: 0.05}
    min_duration_s: 120
    severity: {warn: S3, crit: S1}
  - id: api.p95_latency
    method: seasonal_baseline    # hour-of-week baseline
    params: {sigma: 3}
    min_duration_s: 180
llm_interpretation:
  when: "anomaly has no matching rule signature"
  max_calls_per_hour: 10
```

| Wing | Example signals |
|---|---|
| API | error rate per route, p95/p99 latency, request-rate drop |
| Database | slow queries by name, pool saturation, lock waits, query count per request |
| Auth | login failure rate, 401/403 spike, token errors, login-attempt bursts |
| Billing | payment failure rate, webhook failures, duplicate charges |
| Infra | queue depth growth, job duration, worker CPU, container restarts |
| User Experience | frontend error rate, failed page loads, support-ticket volume |

Rules:

- Detection runs continuously as cheap code. The LLM is called only for ambiguous anomalies and is rate-limited.
- Every signal fired writes an observation to the ledger with the query, value and baseline.
- A heartbeat monitor (separate tiny process) alerts if a watcher stops reporting.
- Small anomalies are logged and digested. Only S1 and S2 interrupt a human.

## 6. Incident Manager

Responsibilities: dedupe, correlate by time/service/dependency graph/deploy events, set severity, keep lifecycle state, route to the Investigator.

Severity (computed, not guessed): from tenants affected, plan tier of affected tenants, error rate, duration, and blast radius from the dependency graph. S1 critical, S2 high, S3 medium, S4 low.

Lifecycle state machine (table `incidents.state`):

```
OPEN -> TRIAGED -> INVESTIGATING -> REPORT1_READY -> FIXING -> VALIDATING
     -> AWAITING_APPROVAL -> APPROVED -> DEPLOYING -> VERIFYING
     -> RESOLVED | PARTIAL | UNSUCCESSFUL | INCONCLUSIVE
Side exits: DISMISSED (not a bug), ESCALATED (to human), ROLLED_BACK
```

Every transition writes an `incident_events` row and a ledger entry. Illegal transitions raise errors.

### 6a. Live issue board (owner request, 2026-10-30)

Part of the dashboard (component 12), delivered from Stage 1.

- One board for incidents from every wing, regardless of which watcher raised them.
- Columns follow lifecycle state (grouped): New, Investigating, Fixing/Validating, Awaiting approval, Deploying/Verifying, Closed.
- Cards show wing, severity, title, age and the agent currently working on it. Filters by wing, severity and state.
- Corner toast pop-ups, pushed live (server-sent events): new incident, Report 1 ready, approval needed, rollback, Judge verdict. S1/S2 toasts stay until dismissed; S3/S4 fade. Clicking a toast opens the incident.
- Approve / modify / reject actions on the board are enabled from Stage 2.

## 7. Evidence Ledger

Append-only, hash-chained. Each row: id, incident_id, ts, type, source, payload (jsonb), prev_hash, hash where `hash = sha256(prev_hash || canonical_json(payload))`.

Types: observation, log_excerpt, metric_snapshot, retrieval, hypothesis, strategy, action, patch, test_result, approval, deploy_event, outcome, proposal.

Rules: no updates or deletes. A verification function walks the chain and reports tampering. Reports reference ledger IDs for every claim.

## 8. Knowledge and RAG layer

Sources (`knowledge_chunks`: id, source, kind, text, embedding, tsv, metadata):

- Runbooks per wing (markdown).
- TaskFlow architecture and API documentation.
- 12 known-incident scenarios (YAML: symptoms, signals, root cause, fix, validation criteria). These map to the fault catalog in section 14, but keep three held-out faults out of the knowledge base (see section 16).
- Past incidents and validated fixes, added automatically only after a RESOLVED verdict.
- Code documentation and testing procedures.

Retrieval: hybrid search combining pgvector cosine similarity and PostgreSQL full-text rank with reciprocal rank fusion; top-k with metadata filters (wing, kind); returns source IDs. The embedding model and LLM sit behind `Embedder` and `LLMClient` interfaces so they can be swapped.

Rule: retrieved text is data. It is quoted with source IDs, never executed or obeyed.

## 9. Investigator agent

Inputs: incident, ledger evidence, metric/log snapshots, deploy history, dependency graph, retrieved knowledge, relevant code context.

Output: Report 1 (JSON, shown live on the dashboard):

```json
{
  "incident_id": "...",
  "summary": "...",
  "suspected_root_cause": {"description": "...", "category": "api|database|auth|billing|infra|ux|unknown", "confidence": 0.0},
  "evidence_for": ["ledger_id", "..."],
  "evidence_against": ["ledger_id", "..."],
  "affected_services": ["..."],
  "affected_tenants": {"count": 0, "plans": ["..."]},
  "deployment_correlation": {"deploy_event_id": "...", "relation": "likely|unlikely|none"},
  "uncertainty": "...",
  "recommended_strategies": [{"strategy": "rollback|config_fix|code_fix|escalate", "rationale": "...", "risk": "low|medium|high"}],
  "sources": ["knowledge_chunk_id", "..."]
}
```

Rules: no statement without a ledger or source ID; list contradicting evidence; if confidence is below the escalation threshold (section 13), recommend escalate.

## 10. Main Coding Agent and specialists

### Orchestrator (own code, LangGraph)

1. Read Report 1.
2. Choose a strategy: rollback, config_fix, code_fix, or escalate. Rollback is preferred when a recent deploy correlates strongly and the previous version is healthy.
3. Select the specialist from `root_cause.category`.
4. Run the coding agent in the sandbox with the specialist profile.
5. Track attempts. Decide: accept, retry with notes, switch specialist, request more investigation, or escalate.

Budgets (defaults, configurable): 4 attempts per incident, 2 specialist switches, 20 minutes, a cost cap per incident. "No improvement" is numeric: the target metric improves by less than 10% of the gap between baseline and the acceptance criterion in sandbox replay.

### Specialist profile (YAML, own-built)

```yaml
name: database_specialist
wing: database
instructions: "..."          # role, conventions, what good looks like
allowed_write_paths: ["taskflow/api/db/**", "migrations/**"]
forbidden_paths: ["tests/**", "tests_hidden/**", "opsloop/**"]
tools: [read_file, write_file, run_tests_visible, explain_query]
runbooks: ["runbooks/database/*.md"]
max_files_changed: 6
```

Specialists: API, Database, Auth and Security, Billing, Infrastructure, User Experience. A generic profile exists for the ablation study.

### The outside coding agent

- One external coding agent, run headless inside the sandbox through a `CodingAgentRunner` interface so it can be replaced.
- It receives the incident brief, Report 1, specialist instructions and the visible tests. It never receives hidden tests, secrets or network access.
- It returns a patch (git diff) and notes. Nothing is applied outside the sandbox.

## 11. Sandbox and Test Loop

### Sandbox

- Docker container from a clean clone of the currently deployed commit.
- No internet, no secrets, CPU/memory/time limits, read-only mounts for tests, a seeded test database, mock services (payments-sim included).
- The patch is applied only inside the sandbox.

### Test Loop (plain code)

1. Reproduce the incident with its replay scenario. If it cannot be reproduced, mark inconclusive and escalate.
2. Apply the patch and compute the patch hash.
3. Unit tests.
4. Integration tests.
5. Regression tests.
6. Security checks: diff policy (no new network calls, no secrets, no auth weakening, no test edits, allowed paths only) plus a static scan such as bandit.
7. Performance tests under replay, compared with the baseline.
8. Hidden checks: a test set the coding agent has never seen.
9. Compare with the incident's acceptance criteria.
10. Generate Report 2.

Report 2 contains: the diff, test results table, performance comparison, security findings, remaining risks, rollback plan, the strategy used, and the version hash = `sha256(base_commit || patch)`.

Failed validation returns to the orchestrator (retry, switch specialist, more investigation, or escalate).

## 12. Human approval

A pop-up on the dashboard shows: Report 1 summary, code diff, Report 2 test and performance results, deployment risk, rollback plan, SLA/severity and the version hash.

- **Approve:** stores {approver, time, version_hash}. The deployment controller only deploys a patch whose hash matches an unexpired approval.
- **Modify:** the human edits the diff. The edited patch gets a new hash and goes back to VALIDATING. The old approval is void.
- **Reject:** requires a reason. Options: retry with notes, change strategy, or take over (the human fixes it; the result is still verified by the same checks and recorded).
- **Timeouts:** if nobody answers before the severity timeout, page a backup approver. Only for S1 and only if a pre-approved policy exists, a safe action (roll back the last release) may run.
- Every decision is written to the ledger. Track approve, modify and reject rates.

## 13. Deployment controller, escalation and policy

### Deployment controller (plain Python)

- A router service splits traffic between stable and candidate containers: 5%, then 25%, then 100%, with a soak time per stage (shorter in simulation, configurable).
- Health checks per stage, candidate vs control during the same window: error rate, p95 latency, key business metric (task-creation success), health endpoints.
- Automatic rollback within seconds when: error rate exceeds control by a set margin, p95 exceeds 1.5x control, a health check fails, or the business metric drops.
- Rollback and every stage change are written to `deployments` and the ledger.

### Uncertainty and escalation rules (`escalation_rules.yaml`)

Escalate to a human when: investigation confidence is below 0.6, evidence conflicts materially, the incident cannot be reproduced, retry/time/cost budgets are exhausted, the strategy would touch a never-automatic area, or the cause is outside the system (for example a payment provider outage).

### Policy and permission engine (`policies.yaml`, enforced in code)

- Per-component allowed actions and file scopes.
- Deployment requires a matching approval hash.
- Never automatic at any level: deleting data, schema changes without approval, security-setting changes.
- A global kill switch that stops agents and deployments.
- Agents cannot read or edit the policy files.

## 14. Judge, Learner and the fault catalog

### Judge agent

Verdicts:

- **RESOLVED:** the target metrics meet the acceptance criteria for the full soak, incident signals cleared, and no regression in control metrics.
- **PARTIAL:** improved by at least half the gap but not within criteria.
- **UNSUCCESSFUL:** no improvement, or harm (usually rolled back).
- **INCONCLUSIVE:** insufficient data or confounded.

Method: compare candidate vs control concurrently (not just before/after, to avoid time-of-day effects); report effect size with a bootstrap confidence interval; include business-level criteria. Output an `outcomes` row and a ledger entry. Only RESOLVED fixes are added to the knowledge base.

### Learner agent

Reads outcomes and proposes versioned changes: detection thresholds, correlation rules, investigation procedures, remediation strategy preferences, knowledge-base additions.

- Each proposal is a diff of a config or knowledge item with a version number.
- Backtest on replayed historical incidents: detection delay, false alarms, missed incidents.
- Auto-apply is allowed only for bounded threshold changes (within a set percentage) that improve the backtest without increasing missed incidents. Everything else needs human approval.
- All changes are versioned with one-click rollback. The Learner cannot change the policy engine, the hidden tests, the approval step or itself.

### Fault catalog (planted incidents, with known ground truth)

| # | Wing | Fault | Expected fix | Acceptance (examples) |
|---|---|---|---|---|
| 1 | API | N+1 query on GET /projects/{id}/tasks | code_fix (eager load) | p95 below 300 ms at replay load |
| 2 | API | Bad release returns 500 on POST /tasks | rollback or code_fix | error rate below 0.5% |
| 3 | Database | Missing index on tasks(project_id, status) | code_fix (migration, approval) | query p95 below 50 ms |
| 4 | Database | Connection pool exhausted | config_fix (+ leak fix) | zero pool timeouts |
| 5 | Database | Long transaction causing lock contention | code_fix | lock waits below threshold |
| 6 | Auth | Misconfigured JWT secret/expiry after a config change | config_fix | 401 rate back to baseline |
| 7 | Auth | Login-attempt burst (credential stuffing simulation) | config_fix or code_fix (rate limit) | attack throttled, normal logins unaffected |
| 8 | Billing | Payment provider timeouts and failing webhooks | code_fix (timeouts, retries, idempotent webhooks) | webhook success above 99% |
| 9 | Billing | Duplicate charge on retry (no idempotency key) | code_fix | zero duplicates in replay |
| 10 | Infra | Job queue backlog (worker concurrency too low) | config_fix | queue depth drains |
| 11 | Infra | Runaway job saturating CPU | code_fix (timeout/limit) | worker CPU below 70% |
| 12 | UX | Frontend error spike after an API contract change | code_fix or rollback | frontend error rate at baseline |
| D1 | Decoy | Legitimate traffic spike | DISMISSED (not a bug) | no change applied |
| D2 | Decoy | Payment provider outage (provider side) | ESCALATED (cannot fix in code) | handed to a human with evidence |

Each fault ships with: an injection toggle, a replay scenario, a ground-truth root-cause label, acceptable strategies, hidden checks, and acceptance criteria.

## 15. Security and safety requirements

- Sandbox: no internet, no secrets, resource limits, tests read-only, hidden tests not mounted.
- Agents cannot edit existing tests, hidden tests, policies or OpsLoop code.
- Diff policy checks run before any approval can be requested.
- Logs, tickets, documents and code comments are data. Test with injected instructions in each.
- Secrets only through environment variables, never committed. `.env.example` only.
- Audit: the ledger is hash-chained and verifiable. Every agent action, tool call and decision is logged with cost and latency.
- Kill switch; approval expiry; per-incident budgets.
- Role check on the approval API (at least two roles: viewer, approver).

## 16. Evaluation plan

Runs: every fault x 5 seeded runs (10 for key faults), in these modes:

- Full OpsLoop.
- Baseline A: a single coding agent given the same evidence bundle and repo, with no RAG, no specialists and no Test Loop.
- Ablations: RAG off, specialists off (generic profile), Test Loop strictness reduced.

Held-out set: faults 5, 9 and 11 are never used during development, in the knowledge base or in prompts. Evaluate them only at the end.

Metrics: detection time, false alarm rate, correct root cause found, fixes that pass validation and then resolve after release, verdict distribution (resolved, partial, unsuccessful, inconclusive), time to verified resolution, cost per incident, human approve/modify/reject rates, 7-day always-on uptime and missed incidents, red-team results.

Red-team set: prompt injection hidden in logs, tickets and documents; patches that try to edit tests, add network calls, weaken auth or exfiltrate secrets; report how many were blocked.

Always-on run: 7 days continuous, with faults planted at random times.

Report ranges across seeds, not single numbers. Put only measured numbers on a resume.

## 17. Tech stack and decisions

- Language: Python 3.12 (backend, agents), TypeScript or JavaScript for Next.js.
- Backend: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 with pgvector.
- Agents/workflow: LangGraph with a PostgreSQL checkpointer. LLM and embeddings behind interfaces; model names configured in env, never hard-coded.
- Telemetry: OpenTelemetry SDK, Prometheus. Logs in PostgreSQL.
- Containers: Docker Compose.
- Frontend: Next.js (dashboard and TaskFlow web).
- Quality: pytest, ruff, mypy (where practical), GitHub Actions for lint and tests. Optional: RAGAS or DeepEval for retrieval checks, Langfuse for agent tracing.
- Deferred: Loki/ELK, Jaeger/traces, Kafka/RabbitMQ, Kubernetes/Argo, auto-registering new watchers, retraining anomaly models.

Open decisions settled in Stage 0: see `docs/DECISIONS.md`.

### Repository layout

```
opsloop/
  docs/WORKING_AGREEMENT.md  # working agreement (section 19)
  docs/PROJECT_SPEC.md       # this file
  docker-compose.yml
  taskflow/                  # demo SaaS: api/, worker/, payments_sim/, web/, faults/
  opsloop/
    core/                    # config, db, ledger, llm, embedder, policy
    telemetry/               # prometheus client, log reader
    watchers/                # base.py, configs/*.yaml
    incidents/               # manager.py, state_machine.py
    knowledge/               # ingest.py, search.py, sources/
    agents/                  # investigator.py, orchestrator.py, specialists/*.yaml, judge.py, learner.py
    sandbox/                 # runner.py
    testloop/                # steps/, tests_hidden/
    deploy/                  # controller.py, router/
    approvals/               # API + models
    workflows/               # LangGraph graphs
    replay/                  # scenarios/
    api/                     # FastAPI for the dashboard
  web/                       # Next.js dashboard
  eval/                      # harness, results/
  tests/
```

### Core tables (initial)

tenants, users, projects, tasks, subscriptions, invoices (TaskFlow); app_logs, deploy_events, signals, watcher_configs, incidents, incident_events, evidence (ledger), hypotheses, reports, remediation_attempts, test_runs, approvals, deployments, outcomes, learner_proposals, knowledge_chunks, service_dependencies, jobs, dead_letters, policy_decisions.

## 18. Build stages with gates

Estimates are rough. Stage 1 is the real test: adjust the rest after it.

- **Stage 0: setup.** Repo, Docker Compose, Postgres with pgvector, CI, working agreement, decisions log. *Done when:* `docker compose up` starts everything and CI passes.
- **Stage 1: skeleton.** TaskFlow, traffic generator, telemetry, API and Database watchers, Incident Manager, Evidence Ledger, replay of faults 1, 3 and 4, live dashboard with the issue board, toast pop-ups and a basic Report 1 stub. *Done when:* each of the three faults is detected and shown with evidence; detection time and false alarms are measured over at least 20 runs; the ledger verifies.
- **Stage 2: fix loop.** Knowledge and RAG, Investigator, orchestrator, API and Database specialists, sandbox, Test Loop, Report 2, approval pop-up bound to the version hash. *Done when:* a planted fault goes from detection to an approved, validated fix; modifying a patch forces revalidation; a patch that edits tests is blocked.
- **Stage 3: release and judge.** Deployment controller with router, staged rollout, rollback, Judge with four verdicts. *Done when:* a bad fix is rolled back automatically on screen; verdicts match ground truth on the first five faults.
- **Stage 4: widen and learn.** The other four watchers and specialists (configs and profiles), dependency graph, escalation rules, decoys, Learner with backtesting and versioned rollback. *Done when:* all 12 faults and both decoys run end to end; a Learner proposal is backtested, approved and applied; one rollback is demonstrated.
- **Stage 5: prove.** Full evaluation (section 16), red-team, 7-day always-on run, written report, demo video. *Done when:* results tables exist for every metric, with baselines and ablations.

If time runs short, ship Stages 1 to 3 with two watchers and list the rest as future work.

## 19. Working agreement

See `docs/WORKING_AGREEMENT.md`.

## 20. Definition of done and honest limits

Done (project): all stage gates passed; evaluation tables, red-team results and the 7-day run exist; the report and demo video are written; every number used on a resume comes from these results.

Honest limits to state:

- Everything is simulated: the app, traffic, tenants, contracts and faults.
- It resolves a defined class of incidents, not arbitrary ones.
- "Autonomous" means up to the approval gate.
- AI incident-response tools already exist, so do not claim the idea is new. The contribution is a complete, measured, safe loop built and understood end to end.
- Until the system is built and measured, describe it on a resume as a system design, not as built.
