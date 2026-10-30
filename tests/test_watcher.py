from pathlib import Path

import pytest

from opsloop.incidents.manager import FiredSignal
from opsloop.watchers.base import Clear, Sample, Watcher
from opsloop.watchers.config import WatcherConfigModel, load_configs


class FakeSource:
    def __init__(self) -> None:
        self.data: dict[str, list[Sample]] = {}

    def query(self, promql: str) -> list[Sample]:
        return self.data.get(promql, [])


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


CFG = WatcherConfigModel.model_validate(
    {
        "watcher": "api",
        "version": 1,
        "service": "taskflow-api",
        "signals": [
            {
                "id": "api.p95",
                "title": "p95 high on {route}",
                "query": "Q",
                "method": "threshold",
                "params": {"warn": 0.3, "crit": 1.0},
                "min_duration_s": 20,
                "clear_after_s": 10,
                "severity": {"warn": "S3", "crit": "S1"},
            }
        ],
    }
)


def test_fires_after_min_duration_with_labels_and_title() -> None:
    src, clock = FakeSource(), Clock()
    w = Watcher(CFG, src, clock)
    src.data["Q"] = [({"route": "/a", "instance": "x:1"}, 0.5), ({"route": "/b"}, 0.1)]
    events = []
    for _ in range(5):
        events += w.tick()
        clock.t += 5
    assert len(events) == 1
    fired = events[0]
    assert isinstance(fired, FiredSignal)
    assert fired.title == "p95 high on /a"
    assert fired.series_key == "route=/a"
    assert fired.severity == "S3"
    assert (fired.fired_at - fired.condition_since).total_seconds() >= 20


def test_escalates_and_clears_when_series_disappears() -> None:
    src, clock = FakeSource(), Clock()
    w = Watcher(CFG, src, clock)
    src.data["Q"] = [({"route": "/a"}, 0.5)]
    for _ in range(5):
        w.tick()
        clock.t += 5
    src.data["Q"] = [({"route": "/a"}, 2.0)]
    escalated = w.tick()
    assert isinstance(escalated[0], FiredSignal) and escalated[0].severity == "S1"
    src.data["Q"] = []
    events = []
    for _ in range(4):
        clock.t += 5
        events += w.tick()
    assert any(isinstance(e, Clear) for e in events)


def test_nan_samples_are_ignored() -> None:
    src, clock = FakeSource(), Clock()
    w = Watcher(CFG, src, clock)
    src.data["Q"] = [({"route": "/a"}, float("nan"))]
    for _ in range(10):
        assert w.tick() == []
        clock.t += 5


def test_shipped_configs_are_valid() -> None:
    cfgs = load_configs()
    assert {c.watcher for c in cfgs} >= {"api", "database"}


def test_stubbed_method_is_rejected(tmp_path: Path) -> None:
    bad = CFG.model_dump()
    bad["signals"][0]["method"] = "seasonal_baseline"
    with pytest.raises(NotImplementedError):
        WatcherConfigModel.model_validate(bad)
