"""Evidence Ledger: append-only, hash-chained record of everything OpsLoop observes and does.

Each row's hash = sha256(prev_hash || canonical_json(envelope)), where the envelope covers
incident_id, ts, type, source and payload (wider than payload alone, so metadata can't be
altered without breaking the chain). Appends are serialised with an advisory lock.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsloop.core.models import Evidence

GENESIS_HASH = "0" * 64
_LEDGER_LOCK_KEY = 0x0B5100B  # arbitrary constant for pg_advisory_xact_lock

EVIDENCE_TYPES = frozenset(
    {
        "observation",
        "log_excerpt",
        "metric_snapshot",
        "retrieval",
        "hypothesis",
        "strategy",
        "action",
        "patch",
        "test_result",
        "approval",
        "deploy_event",
        "outcome",
        "proposal",
    }
)


def canonical_json(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def envelope(
    *, incident_id: int | None, ts: datetime, type: str, source: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if type not in EVIDENCE_TYPES:
        raise ValueError(f"unknown evidence type: {type}")
    return {
        "incident_id": incident_id,
        "ts": ts.astimezone(UTC).isoformat(),
        "type": type,
        "source": source,
        "payload": payload,
    }


def compute_hash(prev_hash: str, env: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(env)).encode("utf-8")).hexdigest()


def append(
    session: Session,
    *,
    type: str,
    source: str,
    payload: dict[str, Any],
    incident_id: int | None = None,
) -> Evidence:
    """Append one entry. The caller owns the transaction; the lock is held until commit."""
    # Round-trip the payload through JSON so the stored jsonb and the hashed form agree.
    payload = json.loads(canonical_json(payload))
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LEDGER_LOCK_KEY})
    prev = session.execute(
        select(Evidence.hash).order_by(Evidence.id.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = prev or GENESIS_HASH
    ts = datetime.now(UTC)
    env = envelope(incident_id=incident_id, ts=ts, type=type, source=source, payload=payload)
    row = Evidence(
        incident_id=incident_id,
        ts=ts,
        type=type,
        source=source,
        payload=payload,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, env),
    )
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str | None = None


def verify(session: Session, batch_size: int = 5000) -> VerifyResult:
    """Walk the whole chain in id order and report the first broken link."""
    prev_hash = GENESIS_HASH
    checked = 0
    last_id = 0
    cols = (
        Evidence.id,
        Evidence.incident_id,
        Evidence.ts,
        Evidence.type,
        Evidence.source,
        Evidence.payload,
        Evidence.prev_hash,
        Evidence.hash,
    )
    while True:
        rows = session.execute(
            select(*cols).where(Evidence.id > last_id).order_by(Evidence.id).limit(batch_size)
        ).all()
        if not rows:
            return VerifyResult(ok=True, checked=checked)
        for row in rows:
            if row.prev_hash != prev_hash:
                return VerifyResult(False, checked, row.id, "prev_hash does not match chain")
            env = envelope(
                incident_id=row.incident_id,
                ts=row.ts,
                type=row.type,
                source=row.source,
                payload=row.payload,
            )
            if compute_hash(prev_hash, env) != row.hash:
                return VerifyResult(False, checked, row.id, "hash does not match contents")
            prev_hash = row.hash
            checked += 1
            last_id = row.id
