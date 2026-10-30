"""Incident lifecycle. Every transition must be listed here; anything else raises."""

STATES: tuple[str, ...] = (
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
    "PARTIAL",
    "UNSUCCESSFUL",
    "INCONCLUSIVE",
    "DISMISSED",
    "ESCALATED",
    "ROLLED_BACK",
)

TERMINAL_STATES = frozenset({"RESOLVED", "PARTIAL", "UNSUCCESSFUL", "INCONCLUSIVE", "DISMISSED"})
ACTIVE_STATES = frozenset(STATES) - TERMINAL_STATES

_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"TRIAGED", "DISMISSED", "ESCALATED"}),
    "TRIAGED": frozenset({"INVESTIGATING", "DISMISSED", "ESCALATED"}),
    "INVESTIGATING": frozenset({"REPORT1_READY", "DISMISSED", "ESCALATED"}),
    "REPORT1_READY": frozenset({"FIXING", "INVESTIGATING", "DISMISSED", "ESCALATED"}),
    "FIXING": frozenset({"VALIDATING", "INVESTIGATING", "ESCALATED"}),
    # VALIDATING -> FIXING is a bounded retry after failed validation.
    "VALIDATING": frozenset({"AWAITING_APPROVAL", "FIXING", "INCONCLUSIVE", "ESCALATED"}),
    # Modify -> VALIDATING (new hash); reject -> FIXING (retry) or ESCALATED (take over).
    "AWAITING_APPROVAL": frozenset({"APPROVED", "VALIDATING", "FIXING", "ESCALATED"}),
    "APPROVED": frozenset({"DEPLOYING", "AWAITING_APPROVAL", "ESCALATED"}),
    "DEPLOYING": frozenset({"VERIFYING", "ROLLED_BACK"}),
    "VERIFYING": frozenset({"RESOLVED", "PARTIAL", "UNSUCCESSFUL", "INCONCLUSIVE", "ROLLED_BACK"}),
    "ROLLED_BACK": frozenset({"FIXING", "UNSUCCESSFUL", "ESCALATED"}),
    "ESCALATED": frozenset({"INVESTIGATING", "FIXING", "DISMISSED"}),
}

# Issue board columns (owner request): every state appears in exactly one column.
BOARD_COLUMNS: dict[str, tuple[str, ...]] = {
    "New": ("OPEN", "TRIAGED"),
    "Investigating": ("INVESTIGATING", "REPORT1_READY"),
    "Fixing": ("FIXING", "VALIDATING"),
    "Awaiting approval": ("AWAITING_APPROVAL", "APPROVED"),
    "Releasing": ("DEPLOYING", "VERIFYING", "ROLLED_BACK"),
    "Escalated": ("ESCALATED",),
    "Closed": ("RESOLVED", "PARTIAL", "UNSUCCESSFUL", "INCONCLUSIVE", "DISMISSED"),
}


class IllegalTransition(Exception):
    pass


def check_transition(src: str, dst: str) -> None:
    if src not in _TRANSITIONS and src not in TERMINAL_STATES:
        raise IllegalTransition(f"unknown state {src!r}")
    if dst not in _TRANSITIONS.get(src, frozenset()):
        raise IllegalTransition(f"{src} -> {dst} is not allowed")
