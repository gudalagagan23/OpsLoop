from datetime import UTC, datetime

import pytest

from opsloop.core.ledger import GENESIS_HASH, canonical_json, compute_hash, envelope


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_hash_depends_on_prev_hash_and_every_envelope_field() -> None:
    ts = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    base = envelope(incident_id=1, ts=ts, type="observation", source="w", payload={"v": 1})
    h = compute_hash(GENESIS_HASH, base)
    assert len(h) == 64
    assert compute_hash("f" * 64, base) != h
    for changed in (
        envelope(incident_id=2, ts=ts, type="observation", source="w", payload={"v": 1}),
        envelope(incident_id=1, ts=ts, type="hypothesis", source="w", payload={"v": 1}),
        envelope(incident_id=1, ts=ts, type="observation", source="x", payload={"v": 1}),
        envelope(incident_id=1, ts=ts, type="observation", source="w", payload={"v": 2}),
    ):
        assert compute_hash(GENESIS_HASH, changed) != h


def test_envelope_normalises_timezone() -> None:
    from datetime import timedelta, timezone

    utc = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    a = envelope(incident_id=None, ts=utc, type="observation", source="w", payload={})
    b = envelope(incident_id=None, ts=ist, type="observation", source="w", payload={})
    assert a == b


def test_unknown_evidence_type_rejected() -> None:
    with pytest.raises(ValueError):
        envelope(incident_id=None, ts=datetime.now(UTC), type="gossip", source="w", payload={})
