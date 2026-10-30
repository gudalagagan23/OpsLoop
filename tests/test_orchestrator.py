from typing import Any

from opsloop.agents.orchestrator import (
    Budgets,
    Decision,
    choose_strategy,
    decide,
    next_specialist,
    notes_from_validation,
)
from opsloop.agents.profiles import load_all, specialist_for
from opsloop.testloop.loop import Step, Validation

PROFILES = load_all()


def _report(
    conf: float = 0.9, recs: tuple[str, ...] = ("code_fix",), dep: str = "none"
) -> dict[str, Any]:
    return {
        "suspected_root_cause": {"confidence": conf, "category": "api", "description": "d"},
        "recommended_strategies": [{"strategy": s, "rationale": "r", "risk": "low"} for s in recs],
        "deployment_correlation": {"relation": dep, "deploy_event_id": None},
    }


def test_low_confidence_escalates() -> None:
    assert choose_strategy(_report(0.5), 0.6)[0] == "escalate"


def test_rollback_only_when_a_deploy_correlates_and_it_was_recommended() -> None:
    assert (
        choose_strategy(_report(recs=("rollback", "code_fix"), dep="likely"), 0.6)[0] == "rollback"
    )
    assert choose_strategy(_report(recs=("rollback", "code_fix"), dep="none"), 0.6)[0] == "rollback"
    assert choose_strategy(_report(recs=("code_fix",), dep="likely"), 0.6)[0] == "code_fix"


def test_first_non_escalate_recommendation_wins() -> None:
    assert choose_strategy(_report(recs=("escalate", "config_fix")), 0.6)[0] == "config_fix"
    assert choose_strategy(_report(recs=("escalate",)), 0.6)[0] == "escalate"


def test_specialist_follows_the_category_and_falls_back_to_generic() -> None:
    assert specialist_for("api", PROFILES) == "api_specialist"
    assert specialist_for("database", PROFILES) == "database_specialist"
    assert specialist_for("billing", PROFILES) == "generic_specialist"


def test_switch_order() -> None:
    assert (
        next_specialist("api_specialist", "api_specialist", ["api_specialist"], PROFILES)
        == "generic_specialist"
    )
    assert (
        next_specialist(
            "generic_specialist",
            "api_specialist",
            ["api_specialist", "generic_specialist"],
            PROFILES,
        )
        is None
    )


def _decide(**kw: Any) -> Decision:
    base: dict[str, Any] = dict(
        verdict="failed",
        gap_closed=0.5,
        attempt_no=1,
        switches=0,
        elapsed_s=10,
        cost_usd=0.1,
        budgets=Budgets(),
        current_specialist="api_specialist",
        category_specialist="api_specialist",
        tried=["api_specialist"],
        profiles=PROFILES,
    )
    base.update(kw)
    return decide(**base)


def test_pass_is_accepted_even_when_over_budget() -> None:
    assert _decide(verdict="passed", cost_usd=99, attempt_no=9).action == "accept"


def test_unreproducible_incident_escalates() -> None:
    assert _decide(verdict="not_reproduced").action == "escalate"


def test_budgets_escalate() -> None:
    assert "cost" in _decide(cost_usd=5.0).reason
    assert "time" in _decide(elapsed_s=1200).reason
    assert "retry budget" in _decide(attempt_no=4).reason
    assert all(
        _decide(**{k: v}).action == "escalate"
        for k, v in (("cost_usd", 6.0), ("elapsed_s", 2000.0), ("attempt_no", 4))
    )


def test_no_improvement_switches_specialist_until_the_switch_budget_is_spent() -> None:
    d = _decide(
        gap_closed=0.05,
        current_specialist="database_specialist",
        category_specialist="database_specialist",
        tried=["database_specialist"],
    )
    assert d.action == "switch" and d.specialist == "generic_specialist"
    assert _decide(gap_closed=0.05, switches=2).action == "retry"


def test_partial_progress_retries_with_the_same_specialist() -> None:
    d = _decide(gap_closed=0.5)
    assert d.action == "retry" and d.specialist == "api_specialist"


def test_mechanical_failures_retry_instead_of_switching() -> None:
    for verdict in ("rejected_by_policy", "patch_does_not_apply", "agent_failed"):
        assert _decide(verdict=verdict, gap_closed=0.0).action == "retry"


def test_notes_explain_policy_and_performance_failures() -> None:
    v = Validation(
        False,
        "rejected_by_policy",
        "h",
        "b",
        [
            Step(
                "diff_policy",
                "failed",
                0,
                {"violations": [{"rule": "test_edit", "detail": "tests may not be edited"}]},
            )
        ],
        {},
        {},
        0.0,
        {},
    )
    assert "test_edit" in notes_from_validation(v)
    v2 = Validation(
        False,
        "failed",
        "h",
        "b",
        [
            Step(
                "performance",
                "failed",
                0,
                {
                    "criteria": [
                        {
                            "passed": False,
                            "label": "queries per list request (p95)",
                            "actual": 102,
                            "op": "<=",
                            "threshold": 5,
                        }
                    ]
                },
            )
        ],
        {},
        {},
        0.0,
        {},
    )
    assert "was 102" in notes_from_validation(v2)
    assert "no patch" in notes_from_validation(None, stopped="error")
