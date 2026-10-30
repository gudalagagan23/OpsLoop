import pytest

from opsloop.incidents.state_machine import (
    ACTIVE_STATES,
    BOARD_COLUMNS,
    STATES,
    TERMINAL_STATES,
    IllegalTransition,
    check_transition,
)


def test_happy_path_is_legal() -> None:
    path = [
        "OPEN",
        "TRIAGED",
        "INVESTIGATING",
        "REPORT1_READY",
        "FIXING",
        "VALIDATING",
        "AWAITING_APPROVAL",
        "APPROVED",
        "DEPLOYING",
        "VERIFYING",
        "RESOLVED",
    ]
    for a, b in zip(path, path[1:], strict=False):
        check_transition(a, b)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        ("OPEN", "RESOLVED"),  # never resolved without validation, deploy and judge
        ("REPORT1_READY", "DEPLOYING"),  # never deploy without approval
        ("VALIDATING", "APPROVED"),  # approval must be requested first
        ("FIXING", "DEPLOYING"),
        ("RESOLVED", "OPEN"),  # terminal
        ("DISMISSED", "TRIAGED"),
    ],
)
def test_illegal_transitions_raise(src: str, dst: str) -> None:
    with pytest.raises(IllegalTransition):
        check_transition(src, dst)


def test_unknown_state_raises() -> None:
    with pytest.raises(IllegalTransition):
        check_transition("OPEN", "PANIC")


def test_modify_goes_back_to_validation() -> None:
    check_transition("AWAITING_APPROVAL", "VALIDATING")


def test_every_state_is_on_exactly_one_board_column() -> None:
    placed = [s for states in BOARD_COLUMNS.values() for s in states]
    assert sorted(placed) == sorted(STATES)
    assert ACTIVE_STATES.isdisjoint(TERMINAL_STATES)
