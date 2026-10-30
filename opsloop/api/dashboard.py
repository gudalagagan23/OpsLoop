"""Read models for the dashboard: command-center overview, live metrics, departments, the
agent pipeline, activity, approvals, ledger, model usage, releases, and the simulation panel.

Everything here reads state; the only writes are the fault toggles in /simulation, which are
approver-only and exist because the whole environment is a simulation."""

import math
import statistics
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsloop.agents.profiles import load_all
from opsloop.api.deps import Db, incident_summary
from opsloop.api.deps import iso as _iso
from opsloop.approvals.service import decision_rates, latest_passed_attempt
from opsloop.core.config import get_settings
from opsloop.core.models import (
    Approval,
    Deployment,
    Evidence,
    Incident,
    IncidentEvent,
    Job,
    KnowledgeChunk,
    LlmCall,
    Outcome,
    RemediationAttempt,
    Report,
    Signal,
    TestRun,
    WatcherConfig,
    WatcherHeartbeat,
)
from opsloop.incidents.state_machine import ACTIVE_STATES, BOARD_COLUMNS

router: APIRouter = APIRouter()

DEPARTMENTS: list[dict[str, Any]] = [
    {
        "wing": "api",
        "name": "API",
        "services": ["taskflow-api"],
        "description": "HTTP routes for projects, tasks and sign-in.",
    },
    {
        "wing": "database",
        "name": "Database",
        "services": ["postgres"],
        "description": "PostgreSQL: queries, indexes, connection pool, locks.",
    },
    {
        "wing": "auth",
        "name": "Auth & Security",
        "services": ["taskflow-api"],
        "description": "Login, JWT tokens, tenant isolation, rate limits.",
    },
    {
        "wing": "billing",
        "name": "Billing",
        "services": ["payments-sim"],
        "description": "Payments, webhooks and invoices (simulated provider).",
    },
    {
        "wing": "infra",
        "name": "Infrastructure",
        "services": ["taskflow-worker"],
        "description": "Background jobs, queues, CPU and containers.",
    },
    {
        "wing": "ux",
        "name": "User Experience",
        "services": ["taskflow-web"],
        "description": "Frontend errors and failed page loads.",
    },
]

# The 17 components of the architecture, in pipeline order.
PIPELINE = [
    ("detect", "Detect", "Watchers"),
    ("triage", "Triage", "Incident Manager"),
    ("investigate", "Investigate", "Investigator"),
    ("fix", "Fix", "Coding agent"),
    ("validate", "Validate", "Test Loop"),
    ("approve", "Approve", "Human"),
    ("release", "Release", "Deployment controller"),
    ("judge", "Judge", "Judge"),
    ("learn", "Learn", "Learner"),
]
_STAGE_OF_STATE = {
    "OPEN": "triage",
    "TRIAGED": "triage",
    "INVESTIGATING": "investigate",
    "REPORT1_READY": "investigate",
    "FIXING": "fix",
    "VALIDATING": "validate",
    "AWAITING_APPROVAL": "approve",
    "APPROVED": "release",
    "DEPLOYING": "release",
    "VERIFYING": "judge",
    "ROLLED_BACK": "release",
    "ESCALATED": "approve",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _ago(dt: datetime | None) -> float | None:
    return None if dt is None else round((_now() - dt).total_seconds(), 1)


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 1) if xs else None


# ---- overview -------------------------------------------------------------------------------


@router.get("/overview")
def overview(db: Db) -> dict[str, Any]:
    since = _now() - timedelta(hours=24)
    active = db.execute(select(Incident).where(Incident.state.in_(ACTIVE_STATES))).scalars().all()
    by_sev = {s: 0 for s in ("S1", "S2", "S3", "S4")}
    by_stage = {key: 0 for key, _, _ in PIPELINE}
    for inc in active:
        by_sev[inc.severity] = by_sev.get(inc.severity, 0) + 1
        by_stage[_STAGE_OF_STATE.get(inc.state, "triage")] += 1
    opened_24h = db.execute(
        select(func.count()).select_from(Incident).where(Incident.created_at >= since)
    ).scalar_one()
    closed_24h = db.execute(
        select(Incident.state, func.count())
        .where(Incident.closed_at >= since)
        .group_by(Incident.state)
    ).all()

    # Time from the first signal to Report 1 (real investigations only) and to approval request.
    r1 = db.execute(
        select(Report.created_at, Incident.first_signal_at)
        .join(Incident, Incident.id == Report.incident_id)
        .where(
            Report.kind == "report1",
            Report.version == 1,
            Report.is_stub.is_(False),
            Report.created_at >= since,
        )
    ).all()
    ar = db.execute(
        select(IncidentEvent.ts, Incident.first_signal_at)
        .join(Incident, Incident.id == IncidentEvent.incident_id)
        .where(IncidentEvent.kind == "approval_requested", IncidentEvent.ts >= since)
    ).all()
    attempts = db.execute(
        select(RemediationAttempt.status, func.count()).group_by(RemediationAttempt.status)
    ).all()
    att = {s: int(n) for s, n in attempts}
    total_att = sum(att.values())
    llm = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(LlmCall.input_tokens + LlmCall.output_tokens), 0),
            func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
        ).where(LlmCall.ts >= since)
    ).one()
    last_model = db.execute(select(LlmCall.model).order_by(LlmCall.id.desc()).limit(1)).scalar()
    return {
        "simulated": True,
        "now": _iso(_now()),
        "incidents": {
            "active": len(active),
            "by_severity": by_sev,
            "by_stage": by_stage,
            "opened_24h": int(opened_24h),
            "closed_24h": {s: int(n) for s, n in closed_24h},
        },
        "approvals_pending": sum(1 for i in active if i.state == "AWAITING_APPROVAL"),
        "escalated": sum(1 for i in active if i.state == "ESCALATED"),
        "fixes": {
            "attempts": total_att,
            "validated": att.get("passed", 0),
            "pass_rate": round(att.get("passed", 0) / total_att, 3) if total_att else None,
        },
        "timings": {
            "median_signal_to_report1_s": _median([(a - b).total_seconds() for a, b in r1]),
            "median_signal_to_approval_s": _median([(a - b).total_seconds() for a, b in ar]),
        },
        "llm": {
            "calls_24h": int(llm[0]),
            "tokens_24h": int(llm[1]),
            "cost_usd_24h": round(float(llm[2]), 4),
            "model": last_model,
        },
        "ledger_entries": int(db.execute(select(func.count()).select_from(Evidence)).scalar_one()),
        "knowledge_chunks": int(
            db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar_one()
        ),
        "pipeline": [
            {"key": k, "label": label, "owner": owner, "count": by_stage[k]}
            for k, label, owner in PIPELINE
        ],
    }


# ---- live metrics (Prometheus) ------------------------------------------------------------------

LIVE_QUERIES = {
    "rps": "sum(rate(http_requests_total[1m]))",
    "error_rate": '(sum(rate(http_requests_total{status=~"5.."}[1m])) or vector(0)) / sum(rate(http_requests_total[1m]))',
    "p95_ms": 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{route!~"/health|/metrics"}[1m]))) * 1000',
    "queries_per_request": 'histogram_quantile(0.95, sum by (le) (rate(db_queries_per_request_bucket{route="/projects/{project_id}/tasks"}[1m])))',
    "pool_in_use": "max(db_pool_in_use)",
}


def _num(v: str) -> float | None:
    x = float(v)
    return None if math.isnan(x) or math.isinf(x) else round(x, 4)


@router.get("/metrics/live")
def live_metrics(minutes: int = 30) -> dict[str, Any]:
    minutes = max(5, min(minutes, 180))
    end = time.time()
    start = end - minutes * 60
    step = max(15, minutes * 2)
    out: dict[str, list[list[float | None]]] = {}
    try:
        with httpx.Client(base_url=get_settings().prometheus_url, timeout=8) as c:
            for name, q in LIVE_QUERIES.items():
                r = c.get(
                    "/api/v1/query_range",
                    params={"query": q, "start": start, "end": end, "step": step},
                )
                r.raise_for_status()
                res = r.json()["data"]["result"]
                out[name] = [[float(t), _num(v)] for t, v in res[0]["values"]] if res else []
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"available": False, "error": str(exc)[:200], "series": {}}
    return {"available": True, "step_s": step, "series": out}


# ---- departments ------------------------------------------------------------------------------


@router.get("/departments")
def departments(db: Db) -> list[dict[str, Any]]:
    beats = {h.watcher: h for h in db.execute(select(WatcherHeartbeat)).scalars()}
    configs = {
        c.watcher: c
        for c in db.execute(select(WatcherConfig).where(WatcherConfig.active)).scalars()
    }
    profiles = load_all()
    since = _now() - timedelta(hours=24)
    out = []
    for d in DEPARTMENTS:
        wing = d["wing"]
        active = (
            db.execute(
                select(Incident)
                .where(Incident.wing == wing, Incident.state.in_(ACTIVE_STATES))
                .order_by(Incident.severity)
            )
            .scalars()
            .all()
        )
        recent = db.execute(
            select(func.count())
            .select_from(Incident)
            .where(Incident.wing == wing, Incident.created_at >= since)
        ).scalar_one()
        hb = beats.get(wing)
        cfg = configs.get(wing)
        monitored = hb is not None and cfg is not None
        stale = hb is not None and _ago(hb.last_seen) is not None and (_ago(hb.last_seen) or 0) > 30
        status = (
            "incident"
            if active
            else "not_monitored"
            if not monitored
            else "stale"
            if stale
            else "healthy"
        )
        out.append(
            {
                **d,
                "status": status,
                "monitored": monitored,
                "watcher": None
                if hb is None
                else {
                    "last_seen": _iso(hb.last_seen),
                    "seconds_ago": _ago(hb.last_seen),
                    "config_version": hb.config_version,
                },
                "signals": []
                if cfg is None
                else [
                    {
                        "id": s["id"],
                        "title": s["title"],
                        "method": s["method"],
                        "severity": s.get("severity", {}),
                    }
                    for s in cfg.config.get("signals", [])
                ],
                "specialist": next((k for k, p in profiles.items() if p.wing == wing), None),
                "active_incidents": [incident_summary(i) for i in active],
                "incidents_24h": int(recent),
                "planned_stage": None if monitored else "Stage 4",
            }
        )
    return out


# ---- agents / pipeline ------------------------------------------------------------------------


def _last(db: Session, col: Any, *where: Any) -> datetime | None:
    q = select(func.max(col))
    for w in where:
        q = q.where(w)
    result: datetime | None = db.execute(q).scalar()
    return result


def _status(last: datetime | None, built: bool = True, window_s: float = 600) -> str:
    if not built:
        return "planned"
    if last is not None and (_now() - last).total_seconds() <= window_s:
        return "active"
    return "ready"


def _probe(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/agents")
def agents(db: Db) -> dict[str, Any]:
    s = get_settings()
    beats = db.execute(select(WatcherHeartbeat)).scalars().all()
    newest_beat = max((h.last_seen for h in beats), default=None)
    jobs = db.execute(
        select(Job.queue, Job.state, func.count()).group_by(Job.queue, Job.state)
    ).all()
    running = db.execute(
        select(Job.queue, Job.payload, Job.locked_at).where(Job.state == "running")
    ).all()
    last_llm = db.execute(select(LlmCall).order_by(LlmCall.id.desc()).limit(1)).scalar_one_or_none()
    tests = db.execute(select(TestRun.passed, func.count()).group_by(TestRun.passed)).all()
    t = {bool(p): int(n) for p, n in tests}
    pending = db.execute(
        select(func.count()).select_from(Incident).where(Incident.state == "AWAITING_APPROVAL")
    ).scalar_one()
    working_on = {q: p.get("incident_id") for q, p, _ in running}

    def comp(
        key: str,
        name: str,
        role: str,
        status: str,
        detail: str,
        last: datetime | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "name": name,
            "role": role,
            "status": status,
            "detail": detail,
            "last_activity": _iso(last),
            **extra,
        }

    report_last = _last(db, Report.created_at, Report.kind == "report1", Report.is_stub.is_(False))
    attempt_last = _last(db, RemediationAttempt.created_at)
    test_last = _last(db, TestRun.created_at)
    approval_last = _last(db, Approval.created_at)
    deploys = db.execute(select(func.count()).select_from(Deployment)).scalar_one()
    outcomes = db.execute(select(func.count()).select_from(Outcome)).scalar_one()
    chunks = db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar_one()
    entries = db.execute(select(func.count()).select_from(Evidence)).scalar_one()
    stale = newest_beat is None or (_now() - newest_beat).total_seconds() > 30

    components = [
        comp(
            "taskflow",
            "TaskFlow app",
            "The monitored SaaS (simulated)",
            "active" if _probe(f"{s.taskflow_url}/health") else "down",
            "API, database, tenants",
            _now(),
        ),
        comp(
            "telemetry",
            "Telemetry",
            "Prometheus metrics + structured logs",
            "active" if _probe(f"{s.prometheus_url}/-/ready") else "down",
            "scraped every 5 s",
            _now(),
        ),
        comp(
            "watchers",
            "Watchers",
            "Detect anomalies with rules and statistics",
            "down" if stale else "active",
            f"{len(beats)} running: " + ", ".join(sorted(h.watcher for h in beats)),
            newest_beat,
        ),
        comp(
            "incident_manager",
            "Incident Manager",
            "Dedupe, correlate, severity, lifecycle",
            _status(_last(db, IncidentEvent.ts)),
            "state machine with 17 states",
            _last(db, IncidentEvent.ts),
        ),
        comp(
            "ledger",
            "Evidence Ledger",
            "Append-only, hash-chained record",
            "active",
            f"{entries} entries",
            _last(db, Evidence.ts),
        ),
        comp(
            "knowledge",
            "Knowledge & RAG",
            "Runbooks, known incidents, past fixes",
            "ready" if chunks else "down",
            f"{chunks} chunks, hybrid search",
        ),
        comp(
            "investigator",
            "Investigator",
            "Root cause + Report 1 (LLM + RAG)",
            "working" if working_on.get("investigate") else _status(report_last),
            f"model {last_llm.model}" if last_llm else "no model calls yet",
            report_last,
            working_on=working_on.get("investigate"),
        ),
        comp(
            "orchestrator",
            "Main Coding Agent",
            "Choose strategy, delegate, retry or escalate",
            "working" if working_on.get("remediate") else _status(attempt_last),
            "LangGraph workflow, Postgres checkpoints",
            attempt_last,
            working_on=working_on.get("remediate"),
        ),
        comp(
            "specialists",
            "Sector specialists",
            "Write the fix inside the sandbox",
            _status(attempt_last),
            ", ".join(sorted(load_all())),
            attempt_last,
        ),
        comp(
            "sandbox",
            "Sandbox + Test Loop",
            "Reproduce, test, hidden checks, Report 2",
            _status(test_last),
            f"{t.get(True, 0)} passed, {t.get(False, 0)} failed",
            test_last,
        ),
        comp(
            "approval",
            "Human approval",
            "Approve the exact validated version",
            "waiting" if pending else _status(approval_last),
            f"{pending} waiting",
            approval_last,
        ),
        comp(
            "deploy",
            "Deployment controller",
            "5% -> 25% -> 100% with auto-rollback",
            "ready",
            f"{deploys} live releases (gate-tested separately)",
        ),
        comp(
            "judge",
            "Judge",
            "Verdict from measured evidence",
            "ready",
            f"{outcomes} verdicts recorded",
        ),
        comp(
            "learner",
            "Learner",
            "Improve thresholds and knowledge from outcomes",
            "planned",
            "Stage 4",
        ),
    ]
    return {
        "components": components,
        "jobs": [{"queue": q, "state": st, "count": int(n)} for q, st, n in jobs],
        "llm": None
        if last_llm is None
        else {
            "model": last_llm.model,
            "agent": last_llm.agent,
            "ts": _iso(last_llm.ts),
            "latency_s": round(last_llm.latency_s, 1),
        },
    }


# ---- activity, approvals, ledger, llm, releases ---------------------------------------------


@router.get("/activity")
def activity(db: Db, limit: int = 40) -> list[dict[str, Any]]:
    rows = db.execute(
        select(IncidentEvent, Incident)
        .join(Incident, Incident.id == IncidentEvent.incident_id)
        .order_by(IncidentEvent.id.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        {
            "event_id": e.id,
            "kind": e.kind,
            "to_state": e.to_state,
            "from_state": e.from_state,
            "actor": e.actor,
            "reason": e.reason,
            "ts": _iso(e.ts),
            "incident": incident_summary(i),
        }
        for e, i in rows
    ]


@router.get("/approvals")
def approvals_queue(db: Db) -> dict[str, Any]:
    waiting = (
        db.execute(
            select(Incident)
            .where(Incident.state == "AWAITING_APPROVAL")
            .order_by(Incident.severity)
        )
        .scalars()
        .all()
    )
    pending = []
    for inc in waiting:
        att = latest_passed_attempt(db, inc.id)
        since = db.execute(
            select(func.max(IncidentEvent.ts)).where(
                IncidentEvent.incident_id == inc.id, IncidentEvent.kind == "approval_requested"
            )
        ).scalar()
        pending.append(
            {
                **incident_summary(inc),
                "version_hash": att.version_hash if att else None,
                "specialist": att.specialist if att else None,
                "waiting_since": _iso(since),
            }
        )
    history = db.execute(
        select(Approval, Incident)
        .join(Incident, Incident.id == Approval.incident_id)
        .order_by(Approval.id.desc())
        .limit(50)
    ).all()
    return {
        "pending": pending,
        "history": [
            {
                "id": a.id,
                "decision": a.decision,
                "approver": a.approver,
                "reason": a.reason,
                "follow_up": a.follow_up,
                "version_hash": a.version_hash,
                "created_at": _iso(a.created_at),
                "expires_at": _iso(a.expires_at),
                "incident": incident_summary(i),
            }
            for a, i in history
        ],
        "rates": decision_rates(db),
    }


@router.get("/ledger/recent")
def ledger_recent(db: Db, limit: int = 60) -> list[dict[str, Any]]:
    rows = db.execute(select(Evidence).order_by(Evidence.id.desc()).limit(max(1, min(limit, 300))))
    return [
        {
            "id": e.id,
            "ts": _iso(e.ts),
            "type": e.type,
            "source": e.source,
            "incident_id": e.incident_id,
            "hash": e.hash,
            "prev_hash": e.prev_hash,
            "payload": e.payload,
        }
        for e in rows.scalars()
    ]


@router.get("/llm/calls")
def llm_calls(db: Db, limit: int = 30) -> list[dict[str, Any]]:
    rows = db.execute(select(LlmCall).order_by(LlmCall.id.desc()).limit(max(1, min(limit, 200))))
    return [
        {
            "id": c.id,
            "ts": _iso(c.ts),
            "agent": c.agent,
            "model": c.model,
            "incident_id": c.incident_id,
            "input_tokens": c.input_tokens,
            "output_tokens": c.output_tokens,
            "cost_usd": c.cost_usd,
            "latency_s": round(c.latency_s, 1),
        }
        for c in rows.scalars()
    ]


@router.get("/releases")
def releases(db: Db) -> dict[str, Any]:
    deps = db.execute(select(Deployment).order_by(Deployment.id.desc()).limit(30)).scalars()
    outs = db.execute(select(Outcome).order_by(Outcome.id.desc()).limit(30)).scalars()
    return {
        "deployments": [
            {
                "id": d.id,
                "incident_id": d.incident_id,
                "version_hash": d.version_hash,
                "kind": d.kind,
                "state": d.state,
                "stage_pct": d.stage_pct,
                "started_at": _iso(d.started_at),
                "finished_at": _iso(d.finished_at),
                "reasons": d.reasons,
            }
            for d in deps
        ],
        "outcomes": [
            {
                "id": o.id,
                "incident_id": o.incident_id,
                "verdict": o.verdict,
                "mode": o.mode,
                "summary": o.summary,
                "explanation": o.explanation,
                "created_at": _iso(o.created_at),
            }
            for o in outs
        ],
    }


@router.get("/signals/recent")
def signals_recent(db: Db, limit: int = 30) -> list[dict[str, Any]]:
    rows = db.execute(select(Signal).order_by(Signal.id.desc()).limit(max(1, min(limit, 200))))
    return [
        {
            "id": x.id,
            "watcher": x.watcher,
            "signal_id": x.signal_id,
            "labels": x.labels,
            "severity": x.severity,
            "value": x.value,
            "baseline": x.baseline,
            "fired_at": _iso(x.fired_at),
            "cleared_at": _iso(x.cleared_at),
            "incident_id": x.incident_id,
        }
        for x in rows.scalars()
    ]


def pending_hash(db: Session, incident_id: int) -> str | None:
    att = latest_passed_attempt(db, incident_id)
    return att.version_hash if att else None


__all__ = ["BOARD_COLUMNS", "router"]
