import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core import jobs
from opsloop.core.models import DeadLetter, Job

pytestmark = pytest.mark.integration


def _q() -> str:
    return f"q{uuid.uuid4().hex[:8]}"


def test_enqueue_is_idempotent(db: Session) -> None:
    q, key = _q(), uuid.uuid4().hex
    first = jobs.enqueue(db, q, {"n": 1}, idempotency_key=key)
    second = jobs.enqueue(db, q, {"n": 2}, idempotency_key=key)
    db.commit()
    assert first is not None and second is None


def test_two_workers_never_claim_the_same_job(db_engine: Engine) -> None:
    q = _q()
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    with sessions() as s:
        for i in range(3):
            jobs.enqueue(s, q, {"i": i})
        s.commit()
    a, b = sessions(), sessions()
    try:
        ja, jb = jobs.claim(a, q), jobs.claim(b, q)  # both transactions open: SKIP LOCKED
        assert ja is not None and jb is not None and ja.id != jb.id
    finally:
        a.rollback(), b.rollback(), a.close(), b.close()


def test_failure_backs_off_then_dead_letters(db: Session) -> None:
    q = _q()
    jobs.enqueue(db, q, {"x": 1}, max_attempts=2)
    db.commit()
    now = datetime.now(UTC)
    job = jobs.claim(db, q, now)
    assert job is not None
    assert jobs.fail(db, job, "boom", now) == "retry"
    db.commit()
    row = db.execute(select(Job).where(Job.id == job.id)).scalar_one()
    assert row.state == "queued" and row.run_after > now
    assert jobs.claim(db, q, now) is None  # not ready yet: backing off
    later = now + timedelta(seconds=jobs.BACKOFF_BASE_S + 1)
    job2 = jobs.claim(db, q, later)
    assert job2 is not None and job2.attempts == 2
    assert jobs.fail(db, job2, "boom again", later) == "dead"
    db.commit()
    dead = db.execute(select(DeadLetter).where(DeadLetter.job_id == job.id)).scalar_one()
    assert dead.error == "boom again"


def test_run_once_retries_a_failing_handler_and_finishes_a_good_one(db_engine: Engine) -> None:
    q = _q()
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    with sessions() as s:
        jobs.enqueue(s, q, {"ok": False}, max_attempts=3)
        s.commit()
    seen: list[dict[str, object]] = []

    def handler(_s: Session, payload: dict[str, object]) -> None:
        seen.append(payload)
        if not payload["ok"]:
            raise RuntimeError("nope")

    assert jobs.run_once(sessions, q, handler) is True
    assert jobs.run_once(sessions, q, handler) is False  # retry is backing off
    with sessions() as s:
        jobs.enqueue(s, q, {"ok": True})
        s.commit()
    assert jobs.run_once(sessions, q, handler) is True
    with sessions() as s:
        states = s.execute(select(Job.state).where(Job.queue == q)).scalars().all()
    assert sorted(states) == ["done", "queued"]
    assert len(seen) == 2


def test_stale_running_job_is_reclaimed(db: Session) -> None:
    q = _q()
    jobs.enqueue(db, q, {})
    now = datetime.now(UTC)
    first = jobs.claim(db, q, now)
    db.commit()
    assert first is not None
    assert jobs.claim(db, q, now) is None
    again = jobs.claim(db, q, now + timedelta(seconds=jobs.STALE_LOCK_S + 1))
    assert again is not None and again.id == first.id and again.attempts == 2
