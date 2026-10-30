import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from opsloop.core import ledger

pytestmark = pytest.mark.integration


def test_append_chains_hashes(db: Session) -> None:
    a = ledger.append(db, type="observation", source="test", payload={"v": 1.5})
    b = ledger.append(db, type="hypothesis", source="test", payload={"text": "x"}, incident_id=7)
    db.commit()
    assert b.prev_hash == a.hash
    assert ledger.verify(db).ok


def test_ledger_rejects_update_and_delete(db: Session) -> None:
    row = ledger.append(db, type="observation", source="test", payload={"v": 2})
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE ops.evidence SET source = 'evil' WHERE id = :id"), {"id": row.id})
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM ops.evidence WHERE id = :id"), {"id": row.id})
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("TRUNCATE ops.evidence"))
    db.rollback()


def test_verify_detects_tampering(db: Session) -> None:
    row = ledger.append(db, type="observation", source="test", payload={"v": 3})
    db.commit()
    # Simulate an attacker with superuser rights bypassing the trigger.
    db.execute(text("ALTER TABLE ops.evidence DISABLE TRIGGER evidence_no_modify"))
    db.execute(
        text("UPDATE ops.evidence SET payload = '{\"v\": 999}' WHERE id = :id"), {"id": row.id}
    )
    result = ledger.verify(db)
    assert not result.ok
    assert result.first_bad_id == row.id
    db.rollback()  # undo the tampering and re-enable the trigger
    assert ledger.verify(db).ok
