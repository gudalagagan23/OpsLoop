"""One Watcher class for every wing; behaviour comes entirely from its YAML config."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from opsloop.incidents.manager import FiredSignal
from opsloop.watchers.config import SignalSpec, WatcherConfigModel
from opsloop.watchers.detectors import Detector, SeriesState, make_detector

Sample = tuple[dict[str, str], float]


class MetricSource(Protocol):
    def query(self, promql: str) -> list[Sample]: ...


class PromClient:
    def __init__(self, base_url: str, timeout_s: float = 5.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def query(self, promql: str) -> list[Sample]:
        r = self._client.get("/api/v1/query", params={"query": promql})
        r.raise_for_status()
        out: list[Sample] = []
        for item in r.json()["data"]["result"]:
            labels = {k: v for k, v in item["metric"].items() if k != "__name__"}
            out.append((labels, float(item["value"][1])))
        return out


@dataclass(frozen=True)
class Clear:
    signal_id: str
    series_key: str
    at: datetime


WatcherEvent = FiredSignal | Clear

_IGNORED_LABELS = {"instance", "job"}


def series_key(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()) if k not in _IGNORED_LABELS)


class _Series:
    def __init__(self, spec: SignalSpec) -> None:
        self.detector: Detector = make_detector(spec.method, spec.params)
        self.state = SeriesState(spec.min_duration_s, spec.clear_after_s)
        self.labels: dict[str, str] = {}
        self.last_value = math.nan
        self.last_baseline: float | None = None
        self.level: str | None = None


class Watcher:
    def __init__(
        self,
        cfg: WatcherConfigModel,
        source: MetricSource,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.source = source
        self.clock = clock
        self._series: dict[tuple[str, str], _Series] = {}

    @property
    def name(self) -> str:
        return self.cfg.watcher

    def tick(self) -> list[WatcherEvent]:
        now = self.clock()
        events: list[WatcherEvent] = []
        for spec in self.cfg.signals:
            seen: set[str] = set()
            for labels, value in self.source.query(spec.query):
                key = series_key(labels)
                seen.add(key)
                series = self._series.setdefault((spec.id, key), _Series(spec))
                series.labels = {k: v for k, v in labels.items() if k not in _IGNORED_LABELS}
                level, baseline = series.detector.level(value)
                if math.isnan(value):
                    continue  # no data: leave the state machine untouched
                series.last_value, series.last_baseline, series.level = value, baseline, level
                events.extend(self._step(spec, key, series, level, now))
            # A series that vanished (e.g. no traffic on a route) counts as quiet.
            for (sid, key), series in self._series.items():
                if sid == spec.id and key not in seen and series.state.fired:
                    events.extend(self._step(spec, key, series, None, now))
        return events

    def _step(
        self, spec: SignalSpec, key: str, s: _Series, level: str | None, now: float
    ) -> list[WatcherEvent]:
        transition = s.state.update(level, now)
        if transition in {"fire", "escalate"}:
            if s.state.fired_level is None or s.state.condition_since is None:
                raise RuntimeError("fired series without level or start time")
            sev = spec.severity.get(s.state.fired_level) or spec.severity.get("warn", "S3")  # type: ignore[call-overload]
            return [
                FiredSignal(
                    watcher=self.cfg.watcher,
                    signal_id=spec.id,
                    series_key=key,
                    labels=s.labels,
                    service=spec.service or self.cfg.service,
                    severity=sev,
                    method=spec.method,
                    query=spec.query,
                    value=s.last_value,
                    baseline=s.last_baseline,
                    condition_since=_dt(s.state.condition_since),
                    fired_at=_dt(now),
                    title=spec.title.format_map(_Default(s.labels)),
                    extra={"level": s.state.fired_level, "config_version": self.cfg.version},
                )
            ]
        if transition == "clear":
            return [Clear(spec.id, key, _dt(now))]
        return []


class _Default(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)
