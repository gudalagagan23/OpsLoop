"""Health comparison for staged rollouts: candidate vs control over the same window.

Plain code, no AI. The rollback rules are the spec's (section 13): error rate above control by
a margin, p95 above 1.5x control, a failed health check, or the business metric dropping."""

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sample:
    """One proxied request, recorded by the router."""

    ts: float
    target: str  # "stable" | "candidate"
    status: int
    ms: float
    kind: str  # "list" | "create" | "other"  (create = POST task: the key business metric)


@dataclass(frozen=True)
class Window:
    n: int
    errors: int
    p95_ms: float
    creates: int
    create_errors: int
    list_n: int
    list_p95_ms: float

    @property
    def error_rate(self) -> float:
        return self.errors / self.n if self.n else 0.0

    @property
    def create_error_rate(self) -> float:
        return self.create_errors / self.creates if self.creates else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def is_error(status: int) -> bool:
    return status >= 500


def summarize(samples: list[Sample]) -> Window:
    creates = [s for s in samples if s.kind == "create"]
    lists = [s for s in samples if s.kind == "list"]
    return Window(
        n=len(samples),
        errors=sum(is_error(s.status) for s in samples),
        p95_ms=percentile([s.ms for s in samples], 0.95),
        creates=len(creates),
        create_errors=sum(is_error(s.status) for s in creates),
        list_n=len(lists),
        list_p95_ms=percentile([s.ms for s in lists], 0.95),
    )


@dataclass(frozen=True)
class Thresholds:
    min_requests: int = 30  # below this the candidate window is too small to judge
    error_margin: float = 0.02  # candidate may exceed control by this much (absolute)
    p95_ratio: float = 1.5  # candidate p95 may be at most this multiple of control's
    p95_floor_ms: float = 100.0  # ignore latency ratios while both are below this
    create_drop: float = 0.05  # candidate create-error rate may exceed control by this much
    min_creates: int = 8


@dataclass(frozen=True)
class HealthDecision:
    ok: bool
    enough_data: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def evaluate(
    candidate: Window, control: Window, health_ok: bool, th: Thresholds | None = None
) -> HealthDecision:
    th = th or Thresholds()
    reasons: list[str] = []
    if not health_ok:
        reasons.append("candidate health endpoint failed")
    enough = candidate.n >= th.min_requests
    if (
        candidate.errors
        and candidate.error_rate > control.error_rate + th.error_margin
        and (enough or candidate.error_rate >= 0.5)
    ):
        reasons.append(
            f"error rate {candidate.error_rate:.1%} exceeds control {control.error_rate:.1%} "
            f"by more than {th.error_margin:.0%}"
        )
    if (
        enough
        and not math.isnan(control.p95_ms)
        and candidate.p95_ms > th.p95_ratio * control.p95_ms
        and candidate.p95_ms > th.p95_floor_ms
    ):
        reasons.append(
            f"p95 latency {candidate.p95_ms:.0f} ms exceeds {th.p95_ratio}x control "
            f"({control.p95_ms:.0f} ms)"
        )
    if (
        candidate.creates >= th.min_creates
        and candidate.create_error_rate > control.create_error_rate + th.create_drop
    ):
        reasons.append(
            f"task-creation failures {candidate.create_error_rate:.1%} vs control "
            f"{control.create_error_rate:.1%}"
        )
    return HealthDecision(ok=not reasons, enough_data=enough, reasons=tuple(reasons))
