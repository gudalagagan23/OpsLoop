"""Test Loop end to end in real Docker sandboxes (slow: ~1 min per validation)."""

import dataclasses

import pytest

from opsloop.agents.profiles import load_profile
from opsloop.replay.scenario import Scenario
from opsloop.sandbox.runner import image_exists
from opsloop.testloop import reference_fixes as rf
from opsloop.testloop.loop import validate, version_hash

pytestmark = [pytest.mark.integration, pytest.mark.sandbox]

DB = load_profile("database_specialist")
API = load_profile("api_specialist")


@pytest.fixture(scope="module", autouse=True)
def _need_docker() -> None:
    try:
        ok = image_exists()
    except (FileNotFoundError, OSError):
        ok = False
    if not ok:
        pytest.skip(
            "sandbox image not built: docker build -f docker/sandbox.Dockerfile -t opsloop-sandbox:dev ."
        )


def _step(v, name):  # type: ignore[no-untyped-def]
    return next(s for s in v.steps if s.name == name)


@pytest.mark.parametrize("fault", ["1", "3", "4"])
def test_reference_fix_passes_every_step(fault: str) -> None:
    sc = Scenario.by_fault(fault)
    v = validate(rf.REFERENCE[fault](), sc, DB)
    failed = [(s.name, s.detail.get("output", s.detail)) for s in v.steps if s.status != "passed"]
    assert v.passed, failed
    names = [s.name for s in v.steps]
    assert names == [
        "diff_policy",
        "reproduce",
        "apply_patch",
        "unit",
        "integration",
        "regression",
        "security_scan",
        "performance",
        "hidden_checks",
    ]
    assert v.gap_closed >= 0.9
    assert v.report2["version_hash"] == version_hash(v.base_commit, rf.REFERENCE[fault]())
    assert v.report2["rollback_plan"] and v.report2["tests"]
    assert _step(v, "reproduce").detail["violated"], "the fault must be reproduced before the fix"


def test_fault3_report_flags_schema_change() -> None:
    v = validate(rf.fix_fault3(), Scenario.by_fault("3"), DB)
    assert v.passed
    assert any("schema" in r for r in v.report2["remaining_risks"])


@pytest.mark.parametrize(
    ("patch_fn", "rule"),
    [
        (rf.bad_edits_visible_test, "test_edit"),
        (rf.bad_adds_network_call, "network_call"),
        (rf.bad_drops_tenant_isolation, "auth_weakening"),
    ],
)
def test_policy_violations_never_reach_a_sandbox(patch_fn, rule: str) -> None:  # type: ignore[no-untyped-def]
    v = validate(patch_fn(), Scenario.by_fault("1"), API)
    assert v.verdict == "rejected_by_policy" and not v.passed
    assert [s.name for s in v.steps] == ["diff_policy"]
    assert rule in {x["rule"] for x in _step(v, "diff_policy").detail["violations"]}


def test_noop_patch_fails_performance() -> None:
    v = validate(rf.bad_noop_comment(), Scenario.by_fault("1"), API)
    assert v.verdict == "failed"
    assert _step(v, "performance").status == "failed"
    assert v.gap_closed < 0.1


def test_fix_that_breaks_the_contract_fails_regression() -> None:
    v = validate(rf.bad_breaks_contract_fault1(), Scenario.by_fault("1"), API)
    assert v.verdict == "failed"
    assert _step(v, "regression").status == "failed"
    assert "hidden_checks" not in [s.name for s in v.steps]


def test_bigger_pool_alone_does_not_fix_the_leak() -> None:
    v = validate(rf.bad_raises_pool_size_only(), Scenario.by_fault("4"), DB)
    assert v.verdict == "failed", v.verdict


def test_unreproducible_incident_is_reported_not_reproduced() -> None:
    sc = dataclasses.replace(Scenario.by_fault("1"), flags=[])
    v = validate(rf.fix_fault1(), sc, API)
    assert v.verdict == "not_reproduced" and not v.passed


def test_patch_that_does_not_apply_is_reported() -> None:
    bad = rf.fix_fault1().replace("legacy_task_loader", "something_else_entirely")
    v = validate(bad, Scenario.by_fault("1"), API)
    assert v.verdict == "patch_does_not_apply"
