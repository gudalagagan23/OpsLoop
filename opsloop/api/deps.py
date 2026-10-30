"""Shared API dependencies: database session, and the incident summary every view uses."""

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core.db import get_engine
from opsloop.core.models import Incident
from opsloop.incidents.state_machine import BOARD_COLUMNS

COLUMN_OF = {state: col for col, states in BOARD_COLUMNS.items() for state in states}
_sessions: sessionmaker[Session] | None = None


def new_session() -> Session:
    global _sessions
    if _sessions is None:
        _sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _sessions()


def get_session() -> Iterator[Session]:
    with new_session() as s:
        yield s


Db = Annotated[Session, Depends(get_session)]


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def default_assignee(state: str) -> str | None:
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


def incident_summary(i: Incident) -> dict[str, Any]:
    return {
        "id": i.id,
        "key": f"INC-{i.id:05d}",
        "title": i.title,
        "wing": i.wing,
        "service": i.service,
        "severity": i.severity,
        "state": i.state,
        "column": COLUMN_OF.get(i.state, "New"),
        "assignee": i.assignee or default_assignee(i.state),
        "signal_count": i.signal_count,
        "created_at": iso(i.created_at),
        "updated_at": iso(i.updated_at),
        "first_signal_at": iso(i.first_signal_at),
        "closed_at": iso(i.closed_at),
        "closed_reason": i.closed_reason,
    }
