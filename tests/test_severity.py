from opsloop.incidents.severity import SeverityInputs, compute_severity


def _inputs(**kw: object) -> SeverityInputs:
    base: dict[str, object] = {
        "signal_severity": "S3",
        "tenants_affected": 0,
        "plans_affected": frozenset(),
        "error_rate": 0.0,
        "duration_s": 0.0,
        "blast_radius": 0,
    }
    base.update(kw)
    return SeverityInputs(**base)  # type: ignore[arg-type]


def test_quiet_warning_stays_low() -> None:
    assert compute_severity(_inputs(signal_severity="S4")).severity == "S4"
    assert compute_severity(_inputs(signal_severity="S3")).severity == "S4"


def test_critical_signal_with_wide_impact_is_s1() -> None:
    result = compute_severity(
        _inputs(
            signal_severity="S1",
            tenants_affected=12,
            plans_affected=frozenset({"enterprise", "free"}),
            error_rate=0.2,
        )
    )
    assert result.severity == "S1"
    assert result.points >= 9


def test_enterprise_impact_raises_severity() -> None:
    plain = compute_severity(_inputs(signal_severity="S2", tenants_affected=1))
    ent = compute_severity(
        _inputs(signal_severity="S2", tenants_affected=1, plans_affected=frozenset({"enterprise"}))
    )
    assert ent.points > plain.points


def test_breakdown_explains_every_point() -> None:
    result = compute_severity(_inputs(signal_severity="S2", duration_s=700, blast_radius=1))
    assert sum(result.breakdown.values()) == result.points
