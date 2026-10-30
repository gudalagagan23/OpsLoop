"""OpsLoop engine: runs the watchers, feeds the Incident Manager, and routes triaged
incidents to the Investigator. Detection is plain code; no LLM calls in Stage 1."""

import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from opsloop.agents.investigator import generate_report1_stub
from opsloop.core.config import get_settings
from opsloop.core.db import get_engine
from opsloop.core.models import Incident, WatcherHeartbeat
from opsloop.incidents.manager import FiredSignal, IncidentManager
from opsloop.watchers.base import Clear, PromClient, Watcher
from opsloop.watchers.config import load_configs, register

log = logging.getLogger("opsloop.engine")


def _heartbeat(session: Session, watcher: Watcher) -> None:
    stmt = insert(WatcherHeartbeat).values(
        watcher=watcher.name, last_seen=datetime.now(UTC), config_version=watcher.cfg.version
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[WatcherHeartbeat.watcher],
            set_={
                "last_seen": stmt.excluded.last_seen,
                "config_version": stmt.excluded.config_version,
            },
        )
    )


def run_once(watchers: list[Watcher], sessions: sessionmaker[Session]) -> None:
    for w in watchers:
        try:
            events = w.tick()
        except Exception:
            # No heartbeat this round: the heartbeat monitor raises an alert if this persists.
            log.exception("watcher %s failed", w.name)
            continue
        with sessions() as s:
            mgr = IncidentManager(s)
            for ev in events:
                if isinstance(ev, FiredSignal):
                    inc = mgr.ingest(ev)
                    log.info("signal %s %s -> incident %s", ev.signal_id, ev.series_key, inc.id)
                elif isinstance(ev, Clear):
                    mgr.clear(ev.signal_id, ev.series_key, ev.at)
            _heartbeat(s, w)
            s.commit()
    with sessions() as s:
        mgr = IncidentManager(s)
        triaged = (
            select(Incident).where(Incident.state == "TRIAGED").with_for_update(skip_locked=True)
        )
        for inc in s.execute(triaged).scalars():
            generate_report1_stub(s, mgr, inc)
        s.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    configs = load_configs()
    with sessions() as s:
        for cfg in configs:
            register(s, cfg)
        s.commit()
    prom = PromClient(settings.prometheus_url)
    watchers = [Watcher(cfg, prom) for cfg in configs]
    interval = min(c.interval_s for c in configs)
    log.info("engine started: %s", ", ".join(f"{c.watcher} v{c.version}" for c in configs))
    while True:
        started = time.monotonic()
        try:
            run_once(watchers, sessions)
        except Exception:
            log.exception("engine iteration failed")
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
