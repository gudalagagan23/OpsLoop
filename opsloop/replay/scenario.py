"""Replay scenarios: planted fault + the flags that arm it in the sandbox + acceptance rules."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCENARIO_DIR = Path(__file__).parent / "scenarios"


@dataclass(frozen=True)
class Scenario:
    id: str
    fault: str
    wing: str
    ground_truth: str
    expected_signals: list[str]
    detect_timeout_s: float = 180
    category: str = "unknown"  # ground-truth root-cause category
    flags: list[str] = field(default_factory=list)  # armed in the sandbox replay
    acceptable_strategies: list[str] = field(default_factory=list)
    acceptance: list[dict[str, Any]] = field(default_factory=list)
    symptoms: str = ""

    @classmethod
    def load_all(cls, directory: Path = SCENARIO_DIR) -> list["Scenario"]:
        return [
            cls(**yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(directory.glob("*.yaml"))
        ]

    @classmethod
    def by_fault(cls, fault: str) -> "Scenario":
        for s in cls.load_all():
            if s.fault == fault:
                return s
        raise KeyError(f"no scenario for fault {fault}")
