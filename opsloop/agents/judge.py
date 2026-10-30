"""Judge: the verdict on whether a deployed fix actually worked, from measured evidence.

The verdict is computed by code (rules + a seeded bootstrap), so it is reproducible and cannot
be talked into "resolved". Four outcomes (spec section 14):

* RESOLVED      target metric meets the acceptance criterion in EVERY stage of the soak, the
                improvement over control is statistically clear, incident signals cleared, and
                no control metric regressed.
* PARTIAL       improved by at least half of the gap to the criterion, but not within it.
* UNSUCCESSFUL  no meaningful improvement, harm, or an automatic rollback.
* INCONCLUSIVE  too little data, a missing control, or signals state unknown (confounded).

Comparison is candidate vs control measured CONCURRENTLY (avoids time-of-day effects). For
changes that also alter what the control sees (a shared-database schema change) the caller
asks for mode "before_after", which compares against a pre-deploy baseline and says so."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from opsloop.deploy.health import Sample, is_error, percentile

RESOLVED, PARTIAL, UNSUCCESSFUL, INCONCLUSIVE = (
    "RESOLVED",
    "PARTIAL",
    "UNSUCCESSFUL",
    "INCONCLUSIVE",
)
GAP_FOR_PARTIAL = 0.5  # spec: "improved by at least half the gap"

MetricFn = Callable[[list[Sample]], float]


def _error_rate(s: list[Sample]) -> float:
    return sum(is_error(x.status) for x in s) / len(s) if s else math.nan


def _create_error_rate(s: list[Sample]) -> float:
    c = [x for x in s if x.kind == "create"]
    return sum(is_error(x.status) for x in c) / len(c) if c else math.nan


def _list_p95(s: list[Sample]) -> float:
    return percentile([x.ms for x in s if x.kind == "list"], 0.95)


def _p95(s: list[Sample]) -> float:
    return percentile([x.ms for x in s], 0.95)


METRICS: dict[str, MetricFn] = {
    "error_rate": _error_rate,
    "create_error_rate": _create_error_rate,
    "list_p95_ms": _list_p95,
    "p95_ms": _p95,
}
# A control metric counts as regressed only if the candidate is worse by more than this.
REGRESSION_MARGIN = {
    "error_rate": 0.01,
    "create_error_rate": 0.03,
    "list_p95_ms": 25.0,
    "p95_ms": 25.0,
}


@dataclass(frozen=True)
class Target:
    """The metric a scenario's fix is meant to move, and the criterion it must meet."""

    metric: str
    op: str  # "<=" (lower is better)
    value: float

    def met(self, x: float) -> bool:
        return not math.isnan(x) and x <= self.value


@dataclass(frozen=True)
class Effect:
    metric: str
    control: float
    candidate: float
    diff: float  # control - candidate: positive = candidate is better (all metrics: lower=better)
    lo: float
    hi: float

    @property
    def clearly_better(self) -> bool:
        return self.lo > 0

    @property
    def clearly_worse(self) -> bool:
        return self.hi < 0


@dataclass
class Judgement:
    verdict: str
    mode: str
    reasons: list[str]
    target: dict[str, Any]
    effects: list[dict[str, Any]] = field(default_factory=list)
    stage_values: dict[int, float] = field(default_factory=dict)
    gap_closed: float | None = None
    candidate_samples: int = 0


def bootstrap_diff(
    control: list[Sample],
    candidate: list[Sample],
    metric: str,
    *,
    rounds: int = 1000,
    seed: int = 1,
    confidence: float = 0.95,
) -> Effect:
    """Effect of the candidate on `metric` with a percentile-bootstrap CI (seeded: repeatable)."""
    fn = METRICS[metric]
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    c0, c1 = fn(control), fn(candidate)
    diffs: list[float] = []
    for _ in range(rounds):
        rc = rng.choices(control, k=len(control))
        rk = rng.choices(candidate, k=len(candidate))
        d = fn(rc) - fn(rk)
        if not math.isnan(d):
            diffs.append(d)
    if not diffs:
        return Effect(metric, c0, c1, math.nan, math.nan, math.nan)
    alpha = (1 - confidence) / 2
    return Effect(metric, c0, c1, c0 - c1, percentile(diffs, alpha), percentile(diffs, 1 - alpha))


def judge(
    *,
    target: Target,
    samples_by_stage: dict[int, list[Sample]],
    rolled_back: bool,
    signals_cleared: bool | None,
    mode: str = "concurrent",
    baseline: list[Sample] | None = None,
    min_candidate_samples: int = 60,
    seed: int = 1,
) -> Judgement:
    """Decide the verdict. `samples_by_stage` maps rollout stage % -> every request seen in it."""
    t = {"metric": target.metric, "op": target.op, "value": target.value}
    if rolled_back:
        return Judgement(UNSUCCESSFUL, mode, ["the release was rolled back automatically"], t)

    cand_by_stage = {
        pct: [s for s in ss if s.target == "candidate"] for pct, ss in samples_by_stage.items()
    }
    candidate_all = [s for ss in cand_by_stage.values() for s in ss]
    if len(candidate_all) < min_candidate_samples:
        return Judgement(
            INCONCLUSIVE,
            mode,
            [
                f"only {len(candidate_all)} candidate requests were measured (need {min_candidate_samples})"
            ],
            t,
            candidate_samples=len(candidate_all),
        )

    if mode == "concurrent":
        control = [s for ss in samples_by_stage.values() for s in ss if s.target == "stable"]
    else:
        control = list(baseline or [])
    if len(control) < min_candidate_samples // 2:
        return Judgement(
            INCONCLUSIVE,
            mode,
            ["no usable control or baseline to compare with"],
            t,
            candidate_samples=len(candidate_all),
        )
    if signals_cleared is None:
        return Judgement(
            INCONCLUSIVE,
            mode,
            ["could not tell whether the incident's signals cleared"],
            t,
            candidate_samples=len(candidate_all),
        )

    fn = METRICS[target.metric]
    stage_values = {pct: fn(ss) for pct, ss in cand_by_stage.items() if len(ss) >= 10}
    effect = bootstrap_diff(control, candidate_all, target.metric, seed=seed)
    effects = [effect]
    for m in METRICS:  # any other metric the candidate made clearly worse?
        if m == target.metric:
            continue
        e = bootstrap_diff(control, candidate_all, m, seed=seed)
        effects.append(e)
    regressions = [
        e.metric
        for e in effects[1:]
        if not math.isnan(e.hi) and e.clearly_worse and -e.diff > REGRESSION_MARGIN[e.metric]
    ]

    base, now = effect.control, effect.candidate
    gap = base - target.value
    gap_closed = (base - now) / gap if gap > 0 else (1.0 if target.met(now) else 0.0)

    reasons: list[str] = []
    meets_all_stages = bool(stage_values) and all(target.met(v) for v in stage_values.values())
    if regressions:
        return Judgement(
            UNSUCCESSFUL,
            mode,
            [f"made other metrics worse: {', '.join(regressions)}"],
            t,
            [_eff(e) for e in effects],
            stage_values,
            gap_closed,
            len(candidate_all),
        )
    if meets_all_stages and signals_cleared and (effect.clearly_better or gap <= 0):
        reasons.append(
            f"{target.metric} met {target.op} {target.value:g} in every stage "
            f"(control {base:.4g} -> candidate {now:.4g}, 95% CI of improvement "
            f"[{effect.lo:.4g}, {effect.hi:.4g}]); signals cleared; no regressions"
        )
        verdict = RESOLVED
    elif meets_all_stages and not signals_cleared:
        verdict = PARTIAL
        reasons.append(
            "the metric is within the criterion but the incident's signals are still firing"
        )
    elif effect.clearly_better and gap_closed >= GAP_FOR_PARTIAL:
        verdict = PARTIAL
        reasons.append(
            f"improved {gap_closed:.0%} of the gap to the criterion, not within it "
            f"(candidate {now:.4g}, needs {target.op} {target.value:g})"
        )
    else:
        verdict = UNSUCCESSFUL
        reasons.append(
            f"no clear improvement: control {base:.4g} vs candidate {now:.4g}, 95% CI of the "
            f"difference [{effect.lo:.4g}, {effect.hi:.4g}]"
        )
    return Judgement(
        verdict,
        mode,
        reasons,
        t,
        [_eff(e) for e in effects],
        stage_values,
        gap_closed,
        len(candidate_all),
    )


def _eff(e: Effect) -> dict[str, Any]:
    return {
        "metric": e.metric,
        "control": e.control,
        "candidate": e.candidate,
        "improvement": e.diff,
        "ci_low": e.lo,
        "ci_high": e.hi,
    }
