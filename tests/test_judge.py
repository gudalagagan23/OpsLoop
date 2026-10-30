import random

from opsloop.agents.judge import (
    INCONCLUSIVE,
    PARTIAL,
    RESOLVED,
    UNSUCCESSFUL,
    Target,
    bootstrap_diff,
    judge,
)
from opsloop.deploy.health import Sample


def gen(
    target: str, n: int, ms: float, err: float = 0.0, seed: int = 0, jitter: float = 0.15
) -> list[Sample]:
    rng = random.Random(seed)  # noqa: S311 - test data, not security
    out = []
    for i in range(n):
        kind = "create" if i % 6 == 0 else "list"
        status = 503 if rng.random() < err else 200
        out.append(Sample(float(i), target, status, ms * (1 + rng.uniform(-jitter, jitter)), kind))
    return out


def stages(cand_ms: float, ctrl_ms: float, cand_err: float = 0.0, ctrl_err: float = 0.0):  # type: ignore[no-untyped-def]
    return {
        5: gen("candidate", 80, cand_ms, cand_err, 1) + gen("stable", 1500, ctrl_ms, ctrl_err, 2),
        25: gen("candidate", 300, cand_ms, cand_err, 3) + gen("stable", 900, ctrl_ms, ctrl_err, 4),
        100: gen("candidate", 600, cand_ms, cand_err, 5),
    }


LIST_P95 = Target("list_p95_ms", "<=", 300)
ERR = Target("error_rate", "<=", 0.005)


def test_a_real_fix_is_resolved_with_a_clear_ci() -> None:
    j = judge(
        target=LIST_P95, samples_by_stage=stages(40, 2600), rolled_back=False, signals_cleared=True
    )
    assert j.verdict == RESOLVED, j.reasons
    eff = j.effects[0]
    assert eff["ci_low"] > 0 and eff["control"] > 2000 and eff["candidate"] < 100
    assert j.gap_closed is not None and j.gap_closed > 0.99


def test_verdict_is_reproducible_for_a_seed() -> None:
    a = judge(
        target=LIST_P95,
        samples_by_stage=stages(40, 2600),
        rolled_back=False,
        signals_cleared=True,
        seed=7,
    )
    b = judge(
        target=LIST_P95,
        samples_by_stage=stages(40, 2600),
        rolled_back=False,
        signals_cleared=True,
        seed=7,
    )
    assert a.effects == b.effects


def test_rollback_is_unsuccessful() -> None:
    j = judge(
        target=LIST_P95,
        samples_by_stage={5: gen("candidate", 10, 40)},
        rolled_back=True,
        signals_cleared=None,
    )
    assert j.verdict == UNSUCCESSFUL and "rolled back" in j.reasons[0]


def test_no_effect_is_unsuccessful() -> None:
    j = judge(
        target=LIST_P95,
        samples_by_stage=stages(2500, 2600),
        rolled_back=False,
        signals_cleared=True,
    )
    assert j.verdict == UNSUCCESSFUL


def test_improved_by_more_than_half_the_gap_but_not_within_criterion_is_partial() -> None:
    # control 2600 ms, candidate 900 ms: closes ~74% of the gap to 300 ms, still above it
    j = judge(
        target=LIST_P95, samples_by_stage=stages(900, 2600), rolled_back=False, signals_cleared=True
    )
    assert j.verdict == PARTIAL, j.reasons
    assert j.gap_closed is not None and 0.5 <= j.gap_closed < 1


def test_within_criterion_but_signals_still_firing_is_partial_not_resolved() -> None:
    j = judge(
        target=LIST_P95, samples_by_stage=stages(40, 2600), rolled_back=False, signals_cleared=False
    )
    assert j.verdict == PARTIAL


def test_unknown_signal_state_is_inconclusive() -> None:
    j = judge(
        target=LIST_P95, samples_by_stage=stages(40, 2600), rolled_back=False, signals_cleared=None
    )
    assert j.verdict == INCONCLUSIVE


def test_too_little_data_is_inconclusive() -> None:
    thin = {5: gen("candidate", 20, 40) + gen("stable", 400, 2600)}
    j = judge(target=LIST_P95, samples_by_stage=thin, rolled_back=False, signals_cleared=True)
    assert j.verdict == INCONCLUSIVE and "only 20" in j.reasons[0]


def test_missing_control_is_inconclusive_in_concurrent_mode() -> None:
    only_cand = {100: gen("candidate", 400, 40)}
    j = judge(target=LIST_P95, samples_by_stage=only_cand, rolled_back=False, signals_cleared=True)
    assert j.verdict == INCONCLUSIVE and "control" in j.reasons[0]


def test_fix_that_meets_target_but_breaks_another_metric_is_unsuccessful() -> None:
    # list latency fixed, but the candidate now errors 6% of the time
    j = judge(
        target=LIST_P95,
        samples_by_stage=stages(40, 2600, cand_err=0.06),
        rolled_back=False,
        signals_cleared=True,
    )
    assert j.verdict == UNSUCCESSFUL and "error_rate" in j.reasons[0]


def test_error_rate_fix_for_a_leaking_pool_is_resolved() -> None:
    j = judge(
        target=ERR,
        samples_by_stage=stages(40, 3000, 0.0, 0.6),
        rolled_back=False,
        signals_cleared=True,
    )
    assert j.verdict == RESOLVED, j.reasons


def test_before_after_mode_uses_the_pre_deploy_baseline_and_says_so() -> None:
    by_stage = {100: gen("candidate", 600, 40, seed=9)}
    baseline = gen("stable", 800, 2600, seed=8)
    j = judge(
        target=LIST_P95,
        samples_by_stage=by_stage,
        rolled_back=False,
        signals_cleared=True,
        mode="before_after",
        baseline=baseline,
    )
    assert j.verdict == RESOLVED and j.mode == "before_after"


def test_bootstrap_ci_straddles_zero_when_there_is_no_difference() -> None:
    e = bootstrap_diff(
        gen("stable", 400, 100, seed=1), gen("candidate", 400, 100, seed=2), "list_p95_ms"
    )
    assert e.lo < 0 < e.hi and not e.clearly_better
