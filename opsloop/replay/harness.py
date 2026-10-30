"""Replay harness: plants faults into the running (simulated) TaskFlow, measures detection time
and false alarms, and writes results under eval/results/. Run from the host:

    python -m opsloop.replay.harness --runs-per-fault 7 --seed 1

It talks to the fault control port (localhost only) and the database; never to agents.
"""

import argparse
import json
import logging
import random
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core import ledger
from opsloop.core.db import get_engine
from opsloop.core.models import Incident, Report, Signal
from opsloop.incidents.manager import dismiss_active

log = logging.getLogger("replay")
SCENARIO_DIR = Path(__file__).parent / "scenarios"
ACTOR = "replay-harness"


@dataclass(frozen=True)
class Scenario:
    id: str
    fault: str
    wing: str
    ground_truth: str
    expected_signals: list[str]
    detect_timeout_s: float = 180

    @classmethod
    def load_all(cls, directory: Path = SCENARIO_DIR) -> list["Scenario"]:
        return [cls(**yaml.safe_load(p.read_text())) for p in sorted(directory.glob("*.yaml"))]


@dataclass
class RunResult:
    round: int
    scenario: str
    fault: str
    fault_on_at: str
    detected: bool
    detection_s: float | None
    first_signal: str | None
    incident_id: int | None
    severity: str | None
    incident_signals: list[str]
    report1_ready: bool
    clean_window_s: float
    false_alarms: list[dict[str, Any]] = field(default_factory=list)
    off_target: list[dict[str, Any]] = field(default_factory=list)


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(results: list[RunResult]) -> dict[str, Any]:
    out: dict[str, Any] = {"scenarios": {}, "overall": {}}
    for sid in sorted({r.scenario for r in results}):
        rs = [r for r in results if r.scenario == sid]
        times = [r.detection_s for r in rs if r.detection_s is not None]
        out["scenarios"][sid] = {
            "runs": len(rs),
            "detected": sum(r.detected for r in rs),
            "detection_rate": sum(r.detected for r in rs) / len(rs),
            "detection_s": None
            if not times
            else {
                "min": round(min(times), 1),
                "median": round(statistics.median(times), 1),
                "p90": round(_pct(times, 0.9), 1),
                "max": round(max(times), 1),
            },
            "off_target_incidents": sum(len(r.off_target) for r in rs),
            "report1_ready": sum(r.report1_ready for r in rs),
        }
    clean_s = sum(r.clean_window_s for r in results)
    fa = sum(len(r.false_alarms) for r in results)
    out["overall"] = {
        "runs": len(results),
        "detected": sum(r.detected for r in results),
        "false_alarms_in_clean_windows": fa,
        "clean_observation_minutes": round(clean_s / 60, 1),
        "false_alarms_per_hour": round(fa / (clean_s / 3600), 3) if clean_s else None,
        "off_target_incidents_during_faults": sum(len(r.off_target) for r in results),
    }
    return out


class Harness:
    def __init__(self, sessions: sessionmaker[Session], control_url: str, seed: int) -> None:
        self.sessions = sessions
        self.control = httpx.Client(base_url=control_url, timeout=30)
        self.rng = random.Random(seed)  # noqa: S311 - schedule jitter, not security
        self.seed = seed

    # ---- helpers ------------------------------------------------------------------------

    def db_now(self) -> datetime:
        with self.sessions() as s:
            now: datetime = s.execute(text("SELECT now()")).scalar_one()
            return now

    def fault(self, fault_id: str, on: bool) -> None:
        self.control.post(f"/faults/{fault_id}/{'on' if on else 'off'}").raise_for_status()

    def open_signal_count(self) -> int:
        with self.sessions() as s:
            return s.execute(
                select(func.count()).select_from(Signal).where(Signal.cleared_at.is_(None))
            ).scalar_one()

    def wait_quiet(self, timeout_s: float = 420) -> None:
        deadline = time.monotonic() + timeout_s
        quiet_polls = 0
        while time.monotonic() < deadline:
            quiet_polls = quiet_polls + 1 if self.open_signal_count() == 0 else 0
            if quiet_polls >= 3:
                return
            time.sleep(5)
        log.warning("signals still open after %.0fs; continuing", timeout_s)

    def dismiss_all(self, reason: str) -> list[int]:
        with self.sessions() as s:
            ids = dismiss_active(s, actor=ACTOR, reason=reason)
            s.commit()
        return ids

    def incidents_between(self, start: datetime, end: datetime | None) -> list[dict[str, Any]]:
        with self.sessions() as s:
            q = select(Incident).where(Incident.created_at >= start)
            if end is not None:
                q = q.where(Incident.created_at < end)
            out = []
            for inc in s.execute(q.order_by(Incident.id)).scalars():
                sigs = s.execute(
                    select(Signal.signal_id, Signal.fired_at).where(Signal.incident_id == inc.id)
                ).all()
                out.append(
                    {
                        "incident_id": inc.id,
                        "title": inc.title,
                        "severity": inc.severity,
                        "signals": sorted({sid for sid, _ in sigs}),
                        "first_fired_at": min(f for _, f in sigs).isoformat() if sigs else None,
                    }
                )
            return out

    # ---- one run ------------------------------------------------------------------------

    def run_one(self, sc: Scenario, rnd: int) -> RunResult:
        self.control.post("/faults/reset").raise_for_status()
        self.wait_quiet()
        self.dismiss_all(f"replay round {rnd}: reset before {sc.id}")

        clean_s = self.rng.uniform(60, 90)
        clean_start = self.db_now()
        time.sleep(clean_s)
        fault_on = self.db_now()
        false_alarms = self.incidents_between(clean_start, fault_on)
        if false_alarms:
            log.warning("false alarm(s) in clean window: %s", false_alarms)
            self.dismiss_all(f"replay round {rnd}: false alarm in clean window")

        self.fault(sc.fault, True)
        log.info("round %d: fault %s (%s) on at %s", rnd, sc.fault, sc.id, fault_on.isoformat())
        detected_at: datetime | None = None
        first_signal: str | None = None
        incident_id: int | None = None
        deadline = time.monotonic() + sc.detect_timeout_s
        while time.monotonic() < deadline:
            with self.sessions() as s:
                hit = s.execute(
                    select(Signal)
                    .where(Signal.fired_at >= fault_on, Signal.signal_id.in_(sc.expected_signals))
                    .order_by(Signal.fired_at)
                    .limit(1)
                ).scalar_one_or_none()
            if hit is not None:
                detected_at, first_signal, incident_id = (
                    hit.fired_at,
                    hit.signal_id,
                    hit.incident_id,
                )
                break
            time.sleep(2)

        report1 = False
        severity = None
        signals: list[str] = []
        if incident_id is not None:
            time.sleep(20)  # let correlated signals attach before snapshotting
            with self.sessions() as s:
                inc = s.get_one(Incident, incident_id)
                severity = inc.severity
                signals = sorted(
                    set(
                        s.execute(
                            select(Signal.signal_id).where(Signal.incident_id == incident_id)
                        ).scalars()
                    )
                )
                report1 = (
                    s.execute(
                        select(Report.id).where(
                            Report.incident_id == incident_id, Report.kind == "report1"
                        )
                    ).first()
                    is not None
                )
        during = self.incidents_between(fault_on, None)
        off_target = [i for i in during if not set(i["signals"]) & set(sc.expected_signals)]
        self.fault(sc.fault, False)
        detection_s = (detected_at - fault_on).total_seconds() if detected_at else None
        log.info(
            "round %d: %s detected=%s in %s s (first signal %s, incident %s, %s)",
            rnd,
            sc.id,
            detected_at is not None,
            None if detection_s is None else round(detection_s, 1),
            first_signal,
            incident_id,
            severity,
        )
        return RunResult(
            round=rnd,
            scenario=sc.id,
            fault=sc.fault,
            fault_on_at=fault_on.isoformat(),
            detected=detected_at is not None,
            detection_s=detection_s,
            first_signal=first_signal,
            incident_id=incident_id,
            severity=severity,
            incident_signals=signals,
            report1_ready=report1,
            clean_window_s=clean_s,
            false_alarms=false_alarms,
            off_target=off_target,
        )

    def run(self, scenarios: list[Scenario], rounds: int, out_dir: Path) -> dict[str, Any]:
        started = datetime.now(UTC)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        out_dir.mkdir(parents=True, exist_ok=True)
        results: list[RunResult] = []
        try:
            for rnd in range(1, rounds + 1):
                for sc in self.rng.sample(scenarios, len(scenarios)):
                    results.append(self.run_one(sc, rnd))
                    self._write(out_dir, stamp, started, results, partial=True)
        finally:
            self.control.post("/faults/reset")
            self.wait_quiet()
            self.dismiss_all("replay finished")
        return self._write(out_dir, stamp, started, results, partial=False)

    def _write(
        self,
        out_dir: Path,
        stamp: str,
        started: datetime,
        results: list[RunResult],
        partial: bool,
    ) -> dict[str, Any]:
        with self.sessions() as s:
            v = ledger.verify(s)
        doc = {
            "simulated": True,
            "stage": 1,
            "seed": self.seed,
            "started_at": started.isoformat(),
            "finished_at": None if partial else datetime.now(UTC).isoformat(),
            "ledger_verified": v.ok,
            "ledger_entries_checked": v.checked,
            "summary": summarize(results) if results else {},
            "runs": [asdict(r) for r in results],
        }
        (out_dir / f"stage1_detection_{stamp}.json").write_text(json.dumps(doc, indent=2))
        return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-per-fault", type=int, default=7)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scenarios", default="", help="comma-separated scenario ids (default all)")
    ap.add_argument("--control-url", default="http://localhost:9100")
    ap.add_argument("--out", default="eval/results")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    scenarios = Scenario.load_all()
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    doc = Harness(sessions, args.control_url, args.seed).run(
        scenarios, args.runs_per_fault, Path(args.out)
    )
    print(json.dumps(doc["summary"], indent=2))
    print("ledger verified:", doc["ledger_verified"])


if __name__ == "__main__":
    main()
