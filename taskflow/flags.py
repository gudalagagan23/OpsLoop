"""Feature flags for TaskFlow (simulated). A flag turns on a legacy code path; the planted
faults are bugs that live behind these flags. Initial flags come from TASKFLOW_FLAGS
(comma-separated); the fault-control port flips them at runtime."""

import os
import threading

_lock = threading.Lock()
_on: set[str] = {f.strip() for f in os.environ.get("TASKFLOW_FLAGS", "").split(",") if f.strip()}


def enabled(name: str) -> bool:
    return name in _on


def set_flag(name: str, on: bool) -> None:
    with _lock:
        (_on.add if on else _on.discard)(name)


def snapshot() -> set[str]:
    return set(_on)


def clear() -> None:
    with _lock:
        _on.clear()
