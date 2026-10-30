"""SQLAlchemy models for the OpsLoop platform tables (schema `ops`)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "ops"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)
    type_annotation_map = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


class AppLog(Base):
    __tablename__ = "app_logs"
    __table_args__ = (Index("ix_app_logs_ts", "ts"), Index("ix_app_logs_level_ts", "level", "ts"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    level: Mapped[str] = mapped_column(String(16))
    service: Mapped[str] = mapped_column(String(64))
    route: Mapped[str | None] = mapped_column(String(256))
    tenant_id: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(default=dict)


class DeployEvent(Base):
    __tablename__ = "deploy_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    service: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))


class ServiceDependency(Base):
    __tablename__ = "service_dependencies"
    __table_args__ = (UniqueConstraint("service", "depends_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(64))
    depends_on: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), default="runtime")


class WatcherConfig(Base):
    __tablename__ = "watcher_configs"
    __table_args__ = (UniqueConstraint("watcher", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    watcher: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WatcherHeartbeat(Base):
    __tablename__ = "watcher_heartbeats"

    watcher: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen: Mapped[datetime]
    config_version: Mapped[int] = mapped_column(Integer)


class Evidence(Base):
    """Append-only, hash-chained ledger row. UPDATE/DELETE/TRUNCATE are blocked by a trigger."""

    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_incident", "incident_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime]
    type: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_state", "state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    wing: Mapped[str] = mapped_column(String(16))
    service: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(2))
    state: Mapped[str] = mapped_column(String(24))
    assignee: Mapped[str | None] = mapped_column(String(64))
    run_tag: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    first_signal_at: Mapped[datetime]
    last_signal_at: Mapped[datetime]
    signal_count: Mapped[int] = mapped_column(Integer, default=0)
    closed_at: Mapped[datetime | None]
    closed_reason: Mapped[str | None] = mapped_column(Text)


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident", "incident_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    kind: Mapped[str] = mapped_column(String(32))  # state_change | signal_attached | report_ready
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str | None] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[int | None] = mapped_column(BigInteger)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_open", "signal_id", "series_key", "cleared_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    watcher: Mapped[str] = mapped_column(String(32))
    signal_id: Mapped[str] = mapped_column(String(64))
    series_key: Mapped[str] = mapped_column(String(256))
    labels: Mapped[dict[str, Any]]
    service: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(2))
    method: Mapped[str] = mapped_column(String(24))
    query: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)
    baseline: Mapped[float | None] = mapped_column(Float)
    condition_since: Mapped[datetime]
    fired_at: Mapped[datetime]
    last_value_at: Mapped[datetime]
    cleared_at: Mapped[datetime | None]
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    evidence_id: Mapped[int | None] = mapped_column(BigInteger)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("incident_id", "kind", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    kind: Mapped[str] = mapped_column(String(16))  # report1 | report2
    version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[dict[str, Any]]
    is_stub: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
