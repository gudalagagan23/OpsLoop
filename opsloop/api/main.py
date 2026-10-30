"""OpsLoop API for the dashboard: issue board, incident detail, live event stream (SSE)."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core import ledger
from opsloop.core.config import get_settings
from opsloop.core.db import DatabaseStatus, check_database, get_engine
from opsloop.core.models import (
    Evidence,
    Incident,
    IncidentEvent,
    Report,
    Signal,
    WatcherConfig,
    WatcherHeartbeat,
)
from opsloop.incidents.state_machine import BOARD_COLUMNS, TERMINAL_STATES

app = FastAPI(title="OpsLoop API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_COLUMN_OF = {state: col for col, states in BOARD_COLUMNS.items() for state in states}
_sessions: sessionmaker[Session] | None = None


def get_db_status() -> DatabaseStatus:
    return check_database()


def _new_session() -> Session:
    global _sessions
    if _sessions is None:
        _sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _sessions()


def get_session() -> Iterator[Session]:
    with _new_session() as s:
        yield s


Db = Annotated[Session, Depends(get_session)]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def incident_summary(i: Incident) -> dict[str, Any]:
    return {
        "id": i.id,
        "key": f"INC-{i.id:05d}",
        "title": i.title,
        "wing": i.wing,
        "service": i.service,
        "severity": i.severity,
        "state": i.state,
        "column": _COLUMN_OF.get(i.state, "New"),
        "assignee": i.assignee or _default_assignee(i.state),
        "signal_count": i.signal_count,
        "created_at": _iso(i.created_at),
        "updated_at": _iso(i.updated_at),
        "first_signal_at": _iso(i.first_signal_at),
        "closed_at": _iso(i.closed_at),
        "closed_reason": i.closed_reason,
    }


def _default_assignee(state: str) -> str | None:
    return {
        "OPEN": "incident-manager",
        "TRIAGED": "incident-manager",
        "INVESTIGATING": "investigator",
        "REPORT1_READY": "awaiting coding agent",
        "FIXING": "coding agent",
        "VALIDATING": "test loop",
        "AWAITING_APPROVAL": "human approver",
        "DEPLOYING": "deployment controller",
        "VERIFYING": "judge",
        "ESCALATED": "human",
    }.get(state)


@app.get("/health")
def health(
    response: Response, db: Annotated[DatabaseStatus, Depends(get_db_status)]
) -> dict[str, Any]:
    ok = db.reachable and db.pgvector
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", "database": db.reachable, "pgvector": db.pgvector}


@app.get("/board")
def board(db: Db, closed_limit: int = 30) -> dict[str, Any]:
    active = db.execute(
        select(Incident)
        .where(Incident.state.not_in(TERMINAL_STATES))
        .order_by(Incident.severity, Incident.updated_at.desc())
    ).scalars()
    closed = db.execute(
        select(Incident)
        .where(Incident.state.in_(TERMINAL_STATES))
        .order_by(Incident.closed_at.desc().nulls_last())
        .limit(closed_limit)
    ).scalars()
    cols: dict[str, list[dict[str, Any]]] = {c: [] for c in BOARD_COLUMNS}
    for inc in [*active, *closed]:
        cols[_COLUMN_OF[inc.state]].append(incident_summary(inc))
    return {
        "columns": [
            {"name": name, "states": list(BOARD_COLUMNS[name]), "incidents": items}
            for name, items in cols.items()
        ],
        "simulated": True,
    }


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: int, db: Db) -> dict[str, Any]:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    signals = db.execute(
        select(Signal).where(Signal.incident_id == incident_id).order_by(Signal.fired_at)
    ).scalars()
    events = db.execute(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.id)
    ).scalars()
    evidence = db.execute(
        select(Evidence).where(Evidence.incident_id == incident_id).order_by(Evidence.id)
    ).scalars()
    report1 = db.execute(
        select(Report)
        .where(Report.incident_id == incident_id, Report.kind == "report1")
        .order_by(Report.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        **incident_summary(inc),
        "signals": [
            {
                "id": s.id,
                "signal_id": s.signal_id,
                "labels": s.labels,
                "service": s.service,
                "severity": s.severity,
                "method": s.method,
                "value": s.value,
                "baseline": s.baseline,
                "fired_at": _iso(s.fired_at),
                "cleared_at": _iso(s.cleared_at),
                "evidence_id": s.evidence_id,
            }
            for s in signals
        ],
        "events": [
            {
                "id": e.id,
                "ts": _iso(e.ts),
                "kind": e.kind,
                "from_state": e.from_state,
                "to_state": e.to_state,
                "actor": e.actor,
                "reason": e.reason,
                "evidence_id": e.evidence_id,
            }
            for e in events
        ],
        "evidence": [
            {
                "id": e.id,
                "ts": _iso(e.ts),
                "type": e.type,
                "source": e.source,
                "payload": e.payload,
                "hash": e.hash,
                "prev_hash": e.prev_hash,
            }
            for e in evidence
        ],
        "report1": None
        if report1 is None
        else {
            "body": report1.body,
            "is_stub": report1.is_stub,
            "created_at": _iso(report1.created_at),
        },
    }


@app.get("/ledger/verify")
def verify_ledger(db: Db) -> dict[str, Any]:
    r = ledger.verify(db)
    return {"ok": r.ok, "checked": r.checked, "first_bad_id": r.first_bad_id, "reason": r.reason}


@app.get("/watchers")
def watchers(db: Db) -> list[dict[str, Any]]:
    active = {
        c.watcher: c.version
        for c in db.execute(select(WatcherConfig).where(WatcherConfig.active)).scalars()
    }
    return [
        {
            "watcher": hb.watcher,
            "last_seen": _iso(hb.last_seen),
            "config_version": hb.config_version,
            "active_version": active.get(hb.watcher),
        }
        for hb in db.execute(select(WatcherHeartbeat).order_by(WatcherHeartbeat.watcher)).scalars()
    ]


def _events_after(last_id: int, limit: int = 100) -> tuple[int, list[dict[str, Any]]]:
    with _new_session() as s:
        rows = s.execute(
            select(IncidentEvent, Incident)
            .join(Incident, Incident.id == IncidentEvent.incident_id)
            .where(IncidentEvent.id > last_id)
            .order_by(IncidentEvent.id)
            .limit(limit)
        ).all()
        out = [
            {
                "event_id": ev.id,
                "kind": ev.kind,
                "to_state": ev.to_state,
                "reason": ev.reason,
                "ts": _iso(ev.ts),
                "incident": incident_summary(inc),
            }
            for ev, inc in rows
        ]
        return (rows[-1][0].id if rows else last_id), out


def _max_event_id() -> int:
    with _new_session() as s:
        return s.execute(select(func.coalesce(func.max(IncidentEvent.id), 0))).scalar_one()


@app.get("/events/stream")
async def event_stream(request: Request) -> StreamingResponse:
    """Server-sent events for live board updates and corner pop-ups."""
    header = request.headers.get("last-event-id")
    last_id = int(header) if header and header.isdigit() else await run_in_threadpool(_max_event_id)

    async def gen() -> AsyncIterator[str]:
        nonlocal last_id
        yield "retry: 3000\n\n"
        idle = 0
        while not await request.is_disconnected():
            last_id, events = await run_in_threadpool(_events_after, last_id)
            for e in events:
                yield f"id: {e['event_id']}\nevent: incident\ndata: {json.dumps(e)}\n\n"
            idle = 0 if events else idle + 1
            if idle >= 15:  # keep proxies from closing an idle stream
                idle = 0
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
