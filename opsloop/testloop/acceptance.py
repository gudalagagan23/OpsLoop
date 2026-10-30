"""Acceptance criteria: compare measured replay metrics with a scenario's rules."""

import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_OPS: dict[str, Callable[[float, float], bool]] = {
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
    "==": operator.eq,
}


@dataclass(frozen=True)
class CriterionResult:
    metric: str
    op: str
    threshold: float
    actual: float
    passed: bool
    label: str


def evaluate(criteria: list[dict[str, Any]], metrics: dict[str, Any]) -> list[CriterionResult]:
    out = []
    for c in criteria:
        actual = float(metrics.get(c["metric"], math.nan))
        ok = not math.isnan(actual) and _OPS[c["op"]](actual, float(c["value"]))
        out.append(
            CriterionResult(
                c["metric"], c["op"], float(c["value"]), actual, ok, c.get("label", c["metric"])
            )
        )
    return out


def gap_closed(
    criteria: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]
) -> float:
    """Fraction of the gap between baseline and the criterion that the patch closed (worst
    criterion decides). 1.0 or more = criterion met; below 0.1 = "no improvement" (spec §10)."""
    worst = math.inf
    for c in criteria:
        name, threshold = c["metric"], float(c["value"])
        b = float(before.get(name, math.nan))
        a = float(after.get(name, math.nan))
        if math.isnan(b) or math.isnan(a):
            return 0.0
        if _OPS[c["op"]](b, threshold):  # already satisfied before the patch: nothing to close
            continue
        gap = abs(b - threshold)
        moved = (b - a) if c["op"] in ("<=", "<", "==") else (a - b)
        worst = min(worst, moved / gap if gap else 1.0)
    return 1.0 if worst is math.inf else worst
