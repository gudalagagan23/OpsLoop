"""Watcher configuration: one YAML file per wing, versioned in ops.watcher_configs."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from opsloop.core.models import WatcherConfig
from opsloop.watchers.detectors import make_detector

CONFIG_DIR = Path(__file__).parent / "configs"
_SEVERITIES = {"S1", "S2", "S3", "S4"}


class SignalSpec(BaseModel):
    id: str
    title: str
    query: str
    method: Literal["threshold", "zscore", "ewma", "seasonal_baseline"]
    params: dict[str, Any] = Field(default_factory=dict)
    min_duration_s: float = 60
    clear_after_s: float = 60
    severity: dict[Literal["warn", "crit"], str]
    service: str | None = None

    @field_validator("severity")
    @classmethod
    def _sev(cls, v: dict[str, str]) -> dict[str, str]:
        if not v or not set(v.values()) <= _SEVERITIES:
            raise ValueError(f"severity values must be among {sorted(_SEVERITIES)}")
        return v

    @model_validator(mode="after")
    def _method_supported(self) -> "SignalSpec":
        make_detector(self.method, self.params)  # raises for unknown / stubbed methods
        return self


class LlmInterpretation(BaseModel):
    when: str
    max_calls_per_hour: int = 10


class WatcherConfigModel(BaseModel):
    watcher: str
    version: int
    service: str
    interval_s: float = 5
    signals: list[SignalSpec]
    # STUB: parsed and versioned, but LLM interpretation of unmatched anomalies is Stage 2+.
    llm_interpretation: LlmInterpretation | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> "WatcherConfigModel":
        ids = [s.id for s in self.signals]
        if len(ids) != len(set(ids)):
            raise ValueError("signal ids must be unique within a watcher")
        return self

    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


def load_configs(directory: Path = CONFIG_DIR) -> list[WatcherConfigModel]:
    return [
        WatcherConfigModel.model_validate(yaml.safe_load(p.read_text()))
        for p in sorted(directory.glob("*.yaml"))
    ]


class ConfigVersionConflict(Exception):
    pass


def register(session: Session, cfg: WatcherConfigModel) -> None:
    """Store this config version and make it the active one. Changing content needs a new
    version number, so every running config is traceable."""
    data = cfg.model_dump(mode="json")
    existing = session.execute(
        select(WatcherConfig).where(
            WatcherConfig.watcher == cfg.watcher, WatcherConfig.version == cfg.version
        )
    ).scalar_one_or_none()
    if existing is not None and existing.config != data:
        raise ConfigVersionConflict(f"{cfg.watcher} v{cfg.version} changed without a version bump")
    session.execute(
        update(WatcherConfig).where(WatcherConfig.watcher == cfg.watcher).values(active=False)
    )
    if existing is None:
        session.add(WatcherConfig(watcher=cfg.watcher, version=cfg.version, config=data))
    else:
        existing.active = True
    session.flush()
