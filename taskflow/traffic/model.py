"""Deterministic (seeded) traffic model for the simulated TaskFlow users.

Arrivals are a non-homogeneous Poisson process (thinning) whose rate follows a compressed
day/night cycle and a weekday/weekend cycle. Same seed + same windows = same events.
"""

import math
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

PLAN_WEIGHTS = {"enterprise": 4.0, "pro": 2.0, "free": 1.0}
DEFAULT_MIX = {
    "list_tasks": 0.45,
    "list_tasks_status": 0.15,
    "list_projects": 0.1,
    "get_task": 0.15,
    "create_task": 0.07,
    "update_task": 0.08,
}


@dataclass(frozen=True)
class Profile:
    base_rps: float = 12.0  # peak weekday rate
    day_period_s: float = 1800.0  # one simulated day lasts 30 real minutes
    start_phase: float = 0.4  # fraction of a day at t=0 (0.4 ~ 09:36)
    night_factor: float = 0.35  # rate at midnight relative to noon
    weekend_factor: float = 0.6
    mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MIX))
    spike_start_s: float | None = None  # decoy D1: legitimate traffic spike
    spike_duration_s: float = 0.0
    spike_factor: float = 1.0

    def without_spike(self) -> "Profile":
        return replace(self, spike_start_s=None)

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
        return cls(**data)


@dataclass(frozen=True)
class Event:
    t: float
    tenant_id: int
    action: str
    choice: float  # uniform [0,1) used to pick project/task/status deterministically


def rate_at(p: Profile, t: float) -> float:
    days = t / p.day_period_s + p.start_phase
    phase = days % 1.0
    diurnal = p.night_factor + (1 - p.night_factor) * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
    weekend = int(days) % 7 >= 5
    rate = p.base_rps * diurnal * (p.weekend_factor if weekend else 1.0)
    if p.spike_start_s is not None and p.spike_start_s <= t < p.spike_start_s + p.spike_duration_s:
        rate *= p.spike_factor
    return rate


class TrafficModel:
    def __init__(self, profile: Profile, seed: int, tenants: list[tuple[int, str]]) -> None:
        self.p = profile
        self.rng = random.Random(seed)  # noqa: S311 - simulation, not security
        self.tenants = [tid for tid, _ in tenants]
        self.weights = [PLAN_WEIGHTS.get(plan, 1.0) for _, plan in tenants]
        self.actions = list(profile.mix)
        self.action_weights = [profile.mix[a] for a in self.actions]
        self._t = 0.0  # time of the last candidate arrival
        self._pending: float | None = None  # arrival drawn past the previous window's end
        spike = profile.spike_factor if profile.spike_start_s is not None else 1.0
        self._max_rate = profile.base_rps * max(1.0, spike)

    def events(self, t0: float, t1: float) -> list[Event]:
        """Events in [t0, t1). Call with contiguous windows to continue the same stream."""
        out: list[Event] = []
        while True:
            if self._pending is not None:
                nxt, self._pending = self._pending, None
            else:
                nxt = max(self._t, t0) + self.rng.expovariate(self._max_rate)
            if nxt >= t1:
                self._pending = nxt  # keeps the stream independent of window boundaries
                break
            self._t = nxt
            if nxt < t0:
                continue  # caller skipped ahead; drop arrivals before the window
            accept = self.rng.random() < rate_at(self.p, nxt) / self._max_rate
            tenant = self.rng.choices(self.tenants, self.weights)[0]
            action = self.rng.choices(self.actions, self.action_weights)[0]
            choice = self.rng.random()
            if accept:
                out.append(Event(nxt, tenant, action, choice))
        return out
