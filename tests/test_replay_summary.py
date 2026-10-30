import pytest

from opsloop.replay.harness import RunResult, Scenario, summarize


def _r(scenario: str, detected: bool, t: float | None, clean: float = 60, fa: int = 0) -> RunResult:
    return RunResult(
        round=1,
        scenario=scenario,
        fault="1",
        fault_on_at="2026-10-30T00:00:00+00:00",
        detected=detected,
        detection_s=t,
        first_signal="x" if detected else None,
        incident_id=1 if detected else None,
        severity="S2" if detected else None,
        incident_signals=[],
        report1_ready=detected,
        clean_window_s=clean,
        false_alarms=[{"incident_id": i} for i in range(fa)],
    )


def test_summary_per_scenario_and_false_alarm_rate() -> None:
    results = [
        _r("a", True, 30),
        _r("a", True, 50),
        _r("a", False, None),
        _r("b", True, 40, clean=1800, fa=1),
    ]
    s = summarize(results)
    assert s["scenarios"]["a"]["detected"] == 2
    assert s["scenarios"]["a"]["detection_s"]["median"] == 40
    assert s["scenarios"]["a"]["detection_rate"] == 2 / 3
    assert s["overall"]["false_alarms_in_clean_windows"] == 1
    # 1 false alarm over 1980 s of clean observation
    assert s["overall"]["false_alarms_per_hour"] == round(1 / (1980 / 3600), 3)


def test_shipped_scenarios_load_and_hold_out_faults_5_9_11() -> None:
    scenarios = Scenario.load_all()
    assert {s.fault for s in scenarios} == {"1", "3", "4"}
    assert not {"5", "9", "11"} & {s.fault for s in scenarios}


def test_sleep_detects_host_suspend(monkeypatch: "pytest.MonkeyPatch") -> None:
    from opsloop.replay import harness as h

    clock = {"t": 1000.0}
    naps = iter([1.0, 1.0, 900.0, 1.0, 1.0])  # third 1 s step "takes" 900 s: lid closed

    monkeypatch.setattr(h.time, "time", lambda: clock["t"])
    monkeypatch.setattr(h.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + next(naps)))
    harness = h.Harness.__new__(h.Harness)
    harness.suspended_for = 0.0
    harness.sleep(5)
    assert harness.suspended_for == 899.0

