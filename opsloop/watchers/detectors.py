"""Cheap, deterministic anomaly detectors. One detector instance per time series."""

import math
from collections import deque
from typing import Any, Protocol

Level = str | None  # None | "warn" | "crit"


class Detector(Protocol):
    def level(self, value: float) -> tuple[Level, float | None]:
        """Return (level, baseline) for a new sample. NaN means no data."""
        ...


class ThresholdDetector:
    def __init__(self, params: dict[str, Any]) -> None:
        self.warn = params.get("warn")
        self.crit = params.get("crit")
        self.below = params.get("direction", "above") == "below"

    def _breach(self, value: float, limit: float | None) -> bool:
        if limit is None:
            return False
        return value <= limit if self.below else value >= limit

    def level(self, value: float) -> tuple[Level, float | None]:
        if math.isnan(value):
            return None, None
        if self._breach(value, self.crit):
            return "crit", self.crit
        if self._breach(value, self.warn):
            return "warn", self.warn
        return None, None


class ZScoreDetector:
    """Rolling z-score against the last `window` normal samples."""

    def __init__(self, params: dict[str, Any]) -> None:
        self.window: deque[float] = deque(maxlen=int(params.get("window", 60)))
        self.min_samples = int(params.get("min_samples", 20))
        self.warn = float(params.get("warn", 3))
        self.crit = float(params.get("crit", 6))
        self.floor = float(params.get("floor", 0.0))
        self.below = params.get("direction", "above") == "below"

    def level(self, value: float) -> tuple[Level, float | None]:
        if math.isnan(value):
            return None, None
        if len(self.window) < self.min_samples:
            self.window.append(value)
            return None, None
        mean = sum(self.window) / len(self.window)
        var = sum((x - mean) ** 2 for x in self.window) / len(self.window)
        std = max(math.sqrt(var), 1e-9)
        delta = (mean - value) if self.below else (value - mean)
        z = delta / std
        lvl: Level = None
        if delta >= self.floor:
            lvl = "crit" if z >= self.crit else "warn" if z >= self.warn else None
        if lvl is None:
            self.window.append(value)  # only normal samples shape the baseline
        return lvl, mean


class EwmaDetector:
    """Exponentially weighted mean/variance; flags value outside mean ± k·std."""

    def __init__(self, params: dict[str, Any]) -> None:
        self.alpha = float(params.get("alpha", 0.05))
        self.k = float(params.get("k", 4))
        self.crit_k = float(params.get("crit_k", self.k * 2))
        self.min_samples = int(params.get("min_samples", 20))
        self.floor = float(params.get("floor", 0.0))
        self.below = params.get("direction", "above") == "below"
        self.mean = 0.0
        self.var = 0.0
        self.n = 0

    def level(self, value: float) -> tuple[Level, float | None]:
        if math.isnan(value):
            return None, None
        if self.n == 0:
            self.mean, self.n = value, 1
            return None, None
        std = math.sqrt(self.var)
        delta = (self.mean - value) if self.below else (value - self.mean)
        lvl: Level = None
        if self.n >= self.min_samples and delta >= self.floor:
            if delta > self.crit_k * std:
                lvl = "crit"
            elif delta > self.k * std:
                lvl = "warn"
        baseline = self.mean
        if lvl is None:
            diff = value - self.mean
            self.mean += self.alpha * diff
            self.var = (1 - self.alpha) * (self.var + self.alpha * diff * diff)
            self.n += 1
        return lvl, baseline


def make_detector(method: str, params: dict[str, Any]) -> Detector:
    if method == "threshold":
        return ThresholdDetector(params)
    if method == "zscore":
        return ZScoreDetector(params)
    if method == "ewma":
        return EwmaDetector(params)
    if method == "seasonal_baseline":
        # STUB: hour-of-week baseline needs a week of history; planned for Stage 4.
        raise NotImplementedError("seasonal_baseline detector is not implemented yet")
    raise ValueError(f"unknown detection method {method!r}")


class SeriesState:
    """Turns a stream of levels into fire / escalate / clear events with hysteresis."""

    def __init__(self, min_duration_s: float, clear_after_s: float) -> None:
        self.min_duration_s = min_duration_s
        self.clear_after_s = clear_after_s
        self.condition_since: float | None = None
        self.quiet_since: float | None = None
        self.fired = False
        self.fired_level: Level = None

    def update(self, level: Level, now: float) -> str | None:
        if level is not None:
            self.quiet_since = None
            if self.condition_since is None:
                self.condition_since = now
            if not self.fired:
                if now - self.condition_since >= self.min_duration_s:
                    self.fired, self.fired_level = True, level
                    return "fire"
                return None
            if level == "crit" and self.fired_level == "warn":
                self.fired_level = "crit"
                return "escalate"
            return None
        if not self.fired:
            self.condition_since = None
            return None
        if self.quiet_since is None:
            self.quiet_since = now
        if now - self.quiet_since >= self.clear_after_s:
            self.fired, self.fired_level = False, None
            self.condition_since = self.quiet_since = None
            return "clear"
        return None
