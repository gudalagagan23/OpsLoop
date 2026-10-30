"""SQLAlchemy models for the OpsLoop platform tables (schema `ops`)."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
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


# ---------------------------------------------------------------------------------------
# Stage 2 tables
# ---------------------------------------------------------------------------------------

EMBEDDING_DIM = 384


class KnowledgeChunk(Base):
    """Knowledge base chunk. The generated `tsv` column is maintained by the database."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("content_hash"),
        Index("ix_knowledge_chunks_kind_wing", "kind", "wing"),
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))  # runbook|architecture|known_incident|...
    wing: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text, ''))"),
        deferred=True,
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_ready", "queue", "state", "run_after"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    queue: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]]
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|dead
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger)
    queue: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]]
    error: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    incident_id: Mapped[int | None] = mapped_column(Integer, index=True)
    agent: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_s: Mapped[float] = mapped_column(Float)
    request_id: Mapped[str | None] = mapped_column(String(64))


class RemediationAttempt(Base):
    __tablename__ = "remediation_attempts"
    __table_args__ = (UniqueConstraint("incident_id", "attempt_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(16))
    specialist: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24))  # agent_failed|rejected_by_policy|failed|passed
    base_commit: Mapped[str | None] = mapped_column(String(64))
    patch: Mapped[str | None] = mapped_column(Text)
    version_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TestRun(Base):
    __test__ = False  # not a pytest class

    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("remediation_attempts.id"))
    version_hash: Mapped[str] = mapped_column(String(64), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    steps: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    version_hash: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # approve|modify|reject
    approver: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    follow_up: Mapped[str | None] = mapped_column(String(24))  # retry|change_strategy|take_over
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime | None]
    evidence_id: Mapped[int | None] = mapped_column(BigInteger)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    incident_id: Mapped[int | None] = mapped_column(Integer, index=True)
    component: Mapped[str] = mapped_column(String(48))
    action: Mapped[str] = mapped_column(String(64))
    allowed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
