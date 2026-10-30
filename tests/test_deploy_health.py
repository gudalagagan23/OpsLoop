import math

from opsloop.deploy.health import Sample, Thresholds, evaluate, percentile, summarize


def _s(target: str, n: int, status: int = 200, ms: float = 40, kind: str = "list") -> list[Sample]:
    return [Sample(float(i), target, status, ms, kind) for i in range(n)]


def test_percentile_interpolates_and_handles_empty() -> None:
    assert percentile([10, 20, 30, 40], 0.5) == 25
    assert percentile([5], 0.95) == 5
    assert math.isnan(percentile([], 0.95))


def test_summarize_counts_errors_and_business_metric() -> None:
    w = summarize(
        _s("candidate", 8)
        + _s("candidate", 2, status=503, kind="create")
        + _s("candidate", 2, kind="create")
    )
    assert (w.n, w.errors, w.creates, w.create_errors) == (12, 2, 4, 2)
    assert w.error_rate == 2 / 12 and w.create_error_rate == 0.5
    assert w.list_n == 8


def test_healthy_candidate_passes() -> None:
    cand, ctrl = summarize(_s("candidate", 60)), summarize(_s("stable", 600))
    d = evaluate(cand, ctrl, health_ok=True)
    assert d.ok and d.enough_data and d.reasons == ()


def test_error_spike_rolls_back() -> None:
    cand = summarize(_s("candidate", 40) + _s("candidate", 20, status=500))
    d = evaluate(cand, summarize(_s("stable", 600)), True)
    assert not d.ok and "error rate" in d.reasons[0]


def test_candidate_that_is_better_than_a_broken_control_passes() -> None:
    ctrl = summarize(_s("stable", 100, status=503))
    assert evaluate(summarize(_s("candidate", 60)), ctrl, True).ok


def test_error_margin_is_respected() -> None:
    cand = summarize(_s("candidate", 99) + _s("candidate", 1, status=500))  # 1% errors
    assert evaluate(cand, summarize(_s("stable", 300)), True).ok


def test_p95_over_one_and_a_half_times_control_rolls_back() -> None:
    d = evaluate(summarize(_s("candidate", 60, ms=400)), summarize(_s("stable", 300, ms=200)), True)
    assert not d.ok and "p95" in d.reasons[0]


def test_latency_ratio_is_ignored_below_the_floor() -> None:
    d = evaluate(summarize(_s("candidate", 60, ms=30)), summarize(_s("stable", 300, ms=10)), True)
    assert d.ok  # 3x slower but both far below the 100 ms floor


def test_failed_health_check_rolls_back_even_with_no_traffic() -> None:
    d = evaluate(summarize([]), summarize(_s("stable", 100)), health_ok=False)
    assert not d.ok and not d.enough_data and "health endpoint" in d.reasons[0]


def test_business_metric_drop_rolls_back() -> None:
    cand = summarize(
        _s("candidate", 30)
        + _s("candidate", 5, status=500, kind="create")
        + _s("candidate", 5, kind="create")
    )
    ctrl = summarize(_s("stable", 300) + _s("stable", 50, kind="create"))
    d = evaluate(cand, ctrl, True, Thresholds(error_margin=0.5))
    assert not d.ok and any("task-creation" in r for r in d.reasons)


def test_small_windows_do_not_trigger_latency_or_small_error_rollbacks() -> None:
    cand = summarize(_s("candidate", 5, ms=900) + _s("candidate", 1, status=500))
    d = evaluate(cand, summarize(_s("stable", 300, ms=100)), True)
    assert d.ok and not d.enough_data


def test_total_failure_rolls_back_immediately_even_in_a_tiny_window() -> None:
    d = evaluate(summarize(_s("candidate", 6, status=500)), summarize(_s("stable", 300)), True)
    assert not d.ok
