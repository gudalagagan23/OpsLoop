"""Staged rollout (plain code): 5% -> 25% -> 100% of traffic with a soak at each stage and
automatic rollback the moment candidate health falls below control.

Router and Backend are small interfaces, so every path is testable without containers."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from opsloop.deploy.health import Sample, Thresholds, Window, evaluate, summarize

log = logging.getLogger("opsloop.rollout")


class Router(Protocol):
    def set_split(self, candidate_pct: int) -> None: ...
    def samples(self, since: float) -> list[Sample]: ...


class Backend(Protocol):
    def start_candidate(self, patch: str, version_hash: str, flags: list[str]) -> str: ...
    def candidate_healthy(self) -> bool: ...
    def stable_healthy(self) -> bool: ...
    def stop_candidate(self) -> None: ...


@dataclass(frozen=True)
class RolloutConfig:
    stages: tuple[int, ...] = (5, 25, 100)
    soak_s: float = 75.0
    poll_s: float = 2.0
    start_timeout_s: float = 90.0
    thresholds: Thresholds = field(default_factory=Thresholds)


@dataclass
class StageRecord:
    pct: int
    started_at: float
    ended_at: float = 0.0
    candidate: Window | None = None
    control: Window | None = None
    samples: list[Sample] = field(default_factory=list)


@dataclass
class RolloutResult:
    outcome: str  # completed | rolled_back
    stage_pct: int  # last stage reached
    reasons: list[str]
    stages: list[StageRecord]
    rollback_seconds: float | None = None  # detection-to-split-zero, when rolled back


StageHook = Callable[[str, int, dict[str, object]], None]


class Rollout:
    def __init__(
        self,
        router: Router,
        backend: Backend,
        cfg: RolloutConfig | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        on_event: StageHook | None = None,
    ) -> None:
        self.router, self.backend = router, backend
        self.cfg = cfg or RolloutConfig()
        self.clock, self.sleep = clock, sleep
        self.on_event = on_event or (lambda *_: None)

    def _rollback(
        self, pct: int, reasons: list[str], stages: list[StageRecord], detected: float
    ) -> RolloutResult:
        self.router.set_split(0)
        took = self.clock() - detected
        self.backend.stop_candidate()
        self.on_event("rolled_back", pct, {"reasons": reasons, "seconds": round(took, 2)})
        log.warning("rolled back at %d%%: %s", pct, reasons)
        return RolloutResult("rolled_back", pct, reasons, stages, rollback_seconds=took)

    def run(self, patch: str, version_hash: str, flags: list[str]) -> RolloutResult:
        cfg = self.cfg
        stages: list[StageRecord] = []
        try:
            self.backend.start_candidate(patch, version_hash, flags)
        except Exception as exc:  # noqa: BLE001 - any start failure means we never took traffic
            self.on_event("rolled_back", 0, {"reasons": [f"candidate failed to start: {exc}"]})
            return RolloutResult("rolled_back", 0, [f"candidate failed to start: {exc}"], stages)
        deadline = self.clock() + cfg.start_timeout_s
        while not self.backend.candidate_healthy():
            if self.clock() > deadline:
                self.backend.stop_candidate()
                reason = "candidate never became healthy"
                self.on_event("rolled_back", 0, {"reasons": [reason]})
                return RolloutResult("rolled_back", 0, [reason], stages)
            self.sleep(cfg.poll_s)
        self.on_event("candidate_ready", 0, {})

        baseline: Window | None = None  # control behaviour seen while it still had traffic
        for pct in cfg.stages:
            self.router.set_split(pct)
            rec = StageRecord(pct, self.clock())
            stages.append(rec)
            self.on_event("stage_started", pct, {})
            while True:
                self.sleep(cfg.poll_s)
                now = self.clock()
                samples = self.router.samples(rec.started_at)
                cand = [s for s in samples if s.target == "candidate"]
                ctrl = [s for s in samples if s.target == "stable"]
                rec.samples = samples
                rec.candidate = summarize(cand)
                control = summarize(ctrl)
                if control.n >= cfg.thresholds.min_requests:
                    baseline = control
                rec.control = control
                compare_to = (
                    control if control.n >= cfg.thresholds.min_requests else (baseline or control)
                )
                healthy = self.backend.candidate_healthy()
                decision = evaluate(rec.candidate, compare_to, healthy, cfg.thresholds)
                if not decision.ok:
                    rec.ended_at = now
                    return self._rollback(pct, list(decision.reasons), stages, detected=now)
                if now - rec.started_at >= cfg.soak_s:
                    rec.ended_at = now
                    break
            self.on_event(
                "stage_passed",
                pct,
                {
                    "candidate_n": rec.candidate.n,
                    "candidate_error_rate": rec.candidate.error_rate,
                    "candidate_p95_ms": rec.candidate.p95_ms,
                },
            )
        return RolloutResult("completed", cfg.stages[-1], [], stages)
