"""Heartbeat monitor: a separate tiny process that raises an incident when a watcher stops
reporting, so a dead watcher can never look like a healthy system."""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core.db import get_engine
from opsloop.core.models import WatcherHeartbeat
from opsloop.incidents.manager import FiredSignal, IncidentManager

log = logging.getLogger("opsloop.heartbeat")
SIGNAL_ID = "watcher.heartbeat_missing"


def check(session: Session, stale_after: timedelta, now: datetime, firing: set[str]) -> None:
    mgr = IncidentManager(session)
    for hb in session.execute(select(WatcherHeartbeat)).scalars():
        stale = now - hb.last_seen > stale_after
        if stale and hb.watcher not in firing:
            firing.add(hb.watcher)
            mgr.ingest(
                FiredSignal(
                    watcher="meta",
                    signal_id=SIGNAL_ID,
                    series_key=f"watcher={hb.watcher}",
                    labels={"watcher": hb.watcher},
                    service="opsloop-engine",
                    severity="S2",
                    method="heartbeat",
                    query="ops.watcher_heartbeats",
                    value=(now - hb.last_seen).total_seconds(),
                    baseline=stale_after.total_seconds(),
                    condition_since=hb.last_seen,
                    fired_at=now,
                    title=f"Watcher {hb.watcher} stopped reporting",
                )
            )
            log.warning("watcher %s heartbeat missing", hb.watcher)
        elif not stale and hb.watcher in firing:
            firing.discard(hb.watcher)
            mgr.clear(SIGNAL_ID, f"watcher={hb.watcher}", now)
    session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    firing: set[str] = set()
    while True:
        try:
            with sessions() as s:
                check(s, timedelta(seconds=30), datetime.now(UTC), firing)
        except Exception:
            log.exception("heartbeat check failed")
        time.sleep(10)


if __name__ == "__main__":
    main()
