"""PostgreSQL job queue: FOR UPDATE SKIP LOCKED claiming, idempotency keys, retries with
exponential backoff, and a dead-letter table. Handlers must be idempotent: a job can run more
than once (a worker may die after doing the work but before marking it done)."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core.models import DeadLetter, Job

log = logging.getLogger("opsloop.jobs")

BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 300.0
STALE_LOCK_S = 600.0  # a running job older than this is assumed to belong to a dead worker


def enqueue(
    session: Session,
    queue: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    max_attempts: int = 5,
    run_after: datetime | None = None,
) -> int | None:
    """Add a job. Returns its id, or None when the idempotency key already exists."""
    stmt = (
        insert(Job)
        .values(
            queue=queue,
            payload=payload,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            run_after=run_after or datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=[Job.idempotency_key])
        .returning(Job.id)
    )
    return session.execute(stmt).scalar_one_or_none()


@dataclass(frozen=True)
class Claimed:
    id: int
    queue: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def claim(session: Session, queue: str, now: datetime | None = None) -> Claimed | None:
    now = now or datetime.now(UTC)
    stale = now - timedelta(seconds=STALE_LOCK_S)
    row = session.execute(
        select(Job)
        .where(
            Job.queue == queue,
            (Job.state == "queued") & (Job.run_after <= now)
            | (Job.state == "running") & (Job.locked_at < stale),
        )
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if row is None:
        return None
    row.state, row.locked_at, row.attempts = "running", now, row.attempts + 1
    session.flush()
    return Claimed(row.id, row.queue, row.payload, row.attempts, row.max_attempts)


def complete(session: Session, job_id: int) -> None:
    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(state="done", finished_at=datetime.now(UTC), locked_at=None)
    )


def fail(session: Session, job: Claimed, error: str, now: datetime | None = None) -> str:
    """Schedule a retry, or move the job to the dead-letter table when attempts are spent."""
    now = now or datetime.now(UTC)
    if job.attempts >= job.max_attempts:
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(state="dead", last_error=error, finished_at=now, locked_at=None)
        )
        session.add(DeadLetter(job_id=job.id, queue=job.queue, payload=job.payload, error=error))
        return "dead"
    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (job.attempts - 1))
    session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            state="queued",
            last_error=error,
            run_after=now + timedelta(seconds=delay),
            locked_at=None,
        )
    )
    return "retry"


Handler = Callable[[Session, dict[str, Any]], None]


def run_once(sessions: sessionmaker[Session], queue: str, handler: Handler) -> bool:
    """Claim and process one job. Returns False when the queue had nothing ready.

    The claim commits first (so the lock is visible and the attempt counted), the handler runs
    in its own transaction, and the outcome is recorded afterwards."""
    with sessions() as s:
        job = claim(s, queue)
        s.commit()
    if job is None:
        return False
    try:
        with sessions() as s:
            handler(s, job.payload)
            s.commit()
    except Exception as exc:
        log.exception("job %s failed (attempt %d/%d)", job.id, job.attempts, job.max_attempts)
        with sessions() as s:
            outcome = fail(s, job, f"{type(exc).__name__}: {exc}")
            s.commit()
        log.warning("job %s -> %s", job.id, outcome)
    else:
        with sessions() as s:
            complete(s, job.id)
            s.commit()
    return True


def work_forever(
    sessions: sessionmaker[Session], handlers: dict[str, Handler], poll_s: float = 1.0
) -> None:
    while True:
        busy = False
        for queue, handler in handlers.items():
            busy = run_once(sessions, queue, handler) or busy
        if not busy:
            time.sleep(poll_s)
