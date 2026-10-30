"""Severity is computed from measured impact, never guessed. Points map to S1..S4."""

from dataclasses import dataclass, field

_SIGNAL_POINTS = {"S1": 6, "S2": 4, "S3": 2, "S4": 0}


@dataclass(frozen=True)
class SeverityInputs:
    signal_severity: str  # worst severity among the incident's signals
    tenants_affected: int
    plans_affected: frozenset[str]
    error_rate: float
    duration_s: float
    blast_radius: int  # services that depend (transitively) on the affected service


@dataclass(frozen=True)
class SeverityResult:
    severity: str
    points: int
    breakdown: dict[str, int] = field(default_factory=dict)


def compute_severity(i: SeverityInputs) -> SeverityResult:
    b: dict[str, int] = {"signal": _SIGNAL_POINTS[i.signal_severity]}
    b["tenants"] = (
        3
        if i.tenants_affected >= 10
        else 2
        if i.tenants_affected >= 3
        else int(i.tenants_affected >= 1)
    )
    b["plan_tier"] = 2 if "enterprise" in i.plans_affected else int("pro" in i.plans_affected)
    b["error_rate"] = 2 if i.error_rate >= 0.05 else int(i.error_rate >= 0.01)
    b["duration"] = 2 if i.duration_s >= 600 else int(i.duration_s >= 180)
    b["blast_radius"] = min(i.blast_radius, 2)
    points = sum(b.values())
    severity = "S1" if points >= 9 else "S2" if points >= 6 else "S3" if points >= 3 else "S4"
    return SeverityResult(severity=severity, points=points, breakdown=b)


def worst(a: str, b: str) -> str:
    return min(a, b)  # "S1" < "S2" < ...
