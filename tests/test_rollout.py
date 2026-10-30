from collections.abc import Callable

from opsloop.deploy.health import Sample
from opsloop.deploy.rollout import Rollout, RolloutConfig


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeRouter:
    """Generates traffic according to the current split. cand_fn/stable_fn make each sample."""

    def __init__(
        self,
        clock: FakeClock,
        cand_fn: Callable[[float], tuple[int, float]],
        stable_fn: Callable[[float], tuple[int, float]] = lambda t: (200, 40.0),
        rps: float = 20.0,
    ) -> None:
        self.clock, self.cand_fn, self.stable_fn, self.rps = clock, cand_fn, stable_fn, rps
        self.pct = 0
        self.splits: list[tuple[float, int]] = []

    def set_split(self, pct: int) -> None:
        self.pct = pct
        self.splits.append((self.clock.t, pct))

    def samples(self, since: float) -> list[Sample]:
        out = []
        n = int((self.clock.t - since) * self.rps)
        for i in range(n):
            ts = since + i / self.rps
            cand = (i % 100) < self.pct
            status, ms = (self.cand_fn if cand else self.stable_fn)(ts)
            kind = "create" if i % 7 == 0 else "list"
            out.append(Sample(ts, "candidate" if cand else "stable", status, ms, kind))
        return out


class FakeBackend:
    def __init__(
        self, healthy: Callable[[], bool] = lambda: True, fail_start: bool = False
    ) -> None:
        self.started = self.stopped = False
        self.healthy, self.fail_start = healthy, fail_start

    def start_candidate(self, patch: str, version_hash: str, flags: list[str]) -> str:
        if self.fail_start:
            raise RuntimeError("image build failed")
        self.started = True
        return "cand"

    def candidate_healthy(self) -> bool:
        return self.started and self.healthy()

    def stable_healthy(self) -> bool:
        return True

    def stop_candidate(self) -> None:
        self.stopped = True


def _run(router: FakeRouter, backend: FakeBackend, clock: FakeClock, **cfg: float) -> object:
    roll = Rollout(
        router, backend, RolloutConfig(soak_s=60, poll_s=2, **cfg), clock=clock, sleep=clock.sleep
    )  # type: ignore[arg-type]
    return roll.run("patch", "hash", [])


def test_healthy_candidate_goes_5_25_100_and_completes() -> None:
    clock = FakeClock()
    router = FakeRouter(clock, lambda t: (200, 40.0))
    res = _run(router, FakeBackend(), clock)
    assert res.outcome == "completed"  # type: ignore[attr-defined]
    assert [p for _, p in router.splits] == [5, 25, 100]
    assert [s.pct for s in res.stages] == [5, 25, 100]  # type: ignore[attr-defined]


def test_errors_roll_back_within_seconds_and_stop_the_candidate() -> None:
    clock = FakeClock()
    router = FakeRouter(clock, lambda t: (500, 40.0))
    backend = FakeBackend()
    res = _run(router, backend, clock)
    assert res.outcome == "rolled_back" and res.stage_pct == 5  # type: ignore[attr-defined]
    assert "error rate" in res.reasons[0]  # type: ignore[attr-defined]
    assert router.pct == 0 and backend.stopped
    assert res.rollback_seconds is not None and res.rollback_seconds <= 2  # type: ignore[attr-defined]


def test_regression_that_only_shows_at_25_percent_still_rolls_back() -> None:
    clock = FakeClock()
    start = clock.t

    def cand(t: float) -> tuple[int, float]:
        return (500, 40.0) if t - start > 100 else (200, 40.0)

    router = FakeRouter(clock, cand)
    res = _run(router, FakeBackend(), clock)
    assert res.outcome == "rolled_back" and res.stage_pct == 25  # type: ignore[attr-defined]
    assert router.pct == 0


def test_slow_candidate_rolls_back_on_p95() -> None:
    clock = FakeClock()
    router = FakeRouter(clock, lambda t: (200, 600.0), lambda t: (200, 200.0))
    res = _run(router, FakeBackend(), clock)
    assert res.outcome == "rolled_back" and "p95" in res.reasons[0]  # type: ignore[attr-defined]


def test_failed_health_check_rolls_back() -> None:
    clock = FakeClock()
    flag = {"up": True}
    start = clock.t

    def healthy() -> bool:
        flag["up"] = clock.t - start < 20
        return flag["up"]

    res = _run(FakeRouter(clock, lambda t: (200, 40.0)), FakeBackend(healthy), clock)
    assert res.outcome == "rolled_back" and "health" in res.reasons[0]  # type: ignore[attr-defined]


def test_candidate_that_never_starts_never_gets_traffic() -> None:
    clock = FakeClock()
    router = FakeRouter(clock, lambda t: (200, 40.0))
    res = _run(router, FakeBackend(fail_start=True), clock)
    assert res.outcome == "rolled_back" and res.stage_pct == 0  # type: ignore[attr-defined]
    assert router.splits == []


def test_candidate_that_never_becomes_healthy_is_stopped() -> None:
    clock = FakeClock()
    backend = FakeBackend(healthy=lambda: False)
    res = _run(FakeRouter(clock, lambda t: (200, 40.0)), backend, clock, start_timeout_s=10)
    assert res.outcome == "rolled_back" and backend.stopped  # type: ignore[attr-defined]


def test_candidate_better_than_a_failing_control_is_promoted() -> None:
    clock = FakeClock()
    router = FakeRouter(clock, lambda t: (200, 40.0), lambda t: (503, 3000.0))
    res = _run(router, FakeBackend(), clock)
    assert res.outcome == "completed"  # type: ignore[attr-defined]


def test_events_are_reported_in_order() -> None:
    clock = FakeClock()
    events: list[tuple[str, int]] = []
    roll = Rollout(
        FakeRouter(clock, lambda t: (200, 40.0)),
        FakeBackend(),
        RolloutConfig(soak_s=10, poll_s=2),
        clock=clock,
        sleep=clock.sleep,
        on_event=lambda k, p, d: events.append((k, p)),
    )
    roll.run("p", "h", [])
    assert events == [
        ("candidate_ready", 0),
        ("stage_started", 5),
        ("stage_passed", 5),
        ("stage_started", 25),
        ("stage_passed", 25),
        ("stage_started", 100),
        ("stage_passed", 100),
    ]
