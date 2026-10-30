import math

import pytest

from opsloop.watchers.detectors import EwmaDetector, SeriesState, ThresholdDetector, ZScoreDetector


def test_threshold_levels_above_and_below() -> None:
    above = ThresholdDetector({"warn": 0.02, "crit": 0.05})
    assert above.level(0.01) == (None, None)
    assert above.level(0.03)[0] == "warn"
    assert above.level(0.06)[0] == "crit"
    below = ThresholdDetector({"warn": 5, "crit": 1, "direction": "below"})
    assert below.level(10)[0] is None
    assert below.level(3)[0] == "warn"
    assert below.level(0.5)[0] == "crit"


def test_zscore_needs_history_then_flags_outliers() -> None:
    z = ZScoreDetector({"window": 30, "min_samples": 10, "warn": 3, "crit": 6})
    for i in range(9):
        assert z.level(10 + (i % 3))[0] is None  # still learning
    for i in range(20):
        z.level(10 + (i % 3))
    level, baseline = z.level(100)
    assert level == "crit"
    assert baseline == pytest.approx(11, abs=0.5)


def test_ewma_ignores_anomalies_when_updating_baseline() -> None:
    e = EwmaDetector({"alpha": 0.2, "k": 4, "min_samples": 5, "floor": 0.0})
    for _ in range(20):
        e.level(1.0 + 0.01)
        e.level(1.0 - 0.01)
    assert e.level(5.0)[0] is not None
    for _ in range(10):
        e.level(5.0)  # sustained anomaly must not become the new normal
    assert e.mean < 1.5


def test_ewma_floor_suppresses_tiny_absolute_shifts() -> None:
    e = EwmaDetector({"alpha": 0.2, "k": 3, "min_samples": 3, "floor": 0.05})
    for _ in range(10):
        e.level(0.001)
    assert e.level(0.02)[0] is None  # 20x relative jump but below the absolute floor


def test_series_fires_only_after_min_duration_and_clears_with_hysteresis() -> None:
    s = SeriesState(min_duration_s=30, clear_after_s=20)
    assert s.update("warn", 0) is None
    assert s.update("warn", 20) is None
    assert s.update("warn", 31) == "fire"
    assert s.update("crit", 35) == "escalate"
    assert s.update(None, 40) is None  # one quiet sample is not enough
    assert s.update("crit", 45) is None  # flapping resets the clear timer
    assert s.update(None, 50) is None
    assert s.update(None, 71) == "clear"
    assert not s.fired


def test_condition_interrupted_before_min_duration_does_not_fire() -> None:
    s = SeriesState(min_duration_s=30, clear_after_s=20)
    s.update("warn", 0)
    s.update(None, 10)
    assert s.update("warn", 35) is None  # timer restarted at 35


def test_nan_is_no_data() -> None:
    assert ThresholdDetector({"warn": 1, "crit": 2}).level(math.nan) == (None, None)
