from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opsloop.core.embedder import HashEmbedder
from opsloop.core.models import KnowledgeChunk
from opsloop.knowledge.ingest import (
    HELD_OUT_FAULTS,
    SOURCES,
    HeldOutFault,
    collect_chunks,
    incident_chunk,
    ingest,
)
from opsloop.knowledge.search import search

pytestmark = pytest.mark.integration


@pytest.fixture
def kb(db: Session) -> Iterator[Session]:
    db.execute(delete(KnowledgeChunk))
    ingest(db, HashEmbedder())
    db.commit()
    yield db
    db.execute(delete(KnowledgeChunk))
    db.commit()


def test_held_out_faults_are_not_in_shipped_sources() -> None:
    faults = {
        str(yaml.safe_load(p.read_text())["fault"]) for p in (SOURCES / "incidents").glob("*.yaml")
    }
    assert not faults & HELD_OUT_FAULTS
    assert {"1", "3", "4"} <= faults
    assert not any(c.meta.get("fault") in HELD_OUT_FAULTS for c in collect_chunks())


@pytest.mark.parametrize("fault", sorted(HELD_OUT_FAULTS))
def test_ingest_refuses_held_out_fault(fault: str) -> None:
    data = {
        "fault": fault,
        "title": "t",
        "symptoms": "s",
        "root_cause": "r",
        "fix": "f",
        "validation": "v",
    }
    with pytest.raises(HeldOutFault):
        incident_chunk("incidents/x.yaml", data)


def test_ingest_is_idempotent_and_removes_stale(kb: Session) -> None:
    again = ingest(kb, HashEmbedder())
    assert again["added"] == 0 and again["removed"] == 0
    kb.add(
        KnowledgeChunk(
            source="runbooks/api.md",
            kind="runbook",
            wing="api",
            title="old",
            text="old text",
            embedding=HashEmbedder().embed(["old"])[0],
            content_hash="x" * 64,
        )
    )
    kb.flush()
    assert ingest(kb, HashEmbedder())["removed"] == 1


def test_hybrid_search_finds_n_plus_one_runbook_and_incident(kb: Session) -> None:
    hits = search(
        kb, HashEmbedder(), "p95 latency high with many queries per request N+1 loop", k=5
    )
    titles = " | ".join(h.title for h in hits)
    assert "N+1" in titles or "latency" in titles.lower()
    assert any(h.kind == "known_incident" and h.meta.get("fault") == "1" for h in hits)


def test_search_filters_by_wing_and_kind(kb: Session) -> None:
    hits = search(
        kb,
        HashEmbedder(),
        "connection pool exhausted timeouts",
        k=10,
        wing="database",
        kinds=["known_incident"],
    )
    assert hits
    assert all(h.kind == "known_incident" and h.wing in ("database", None) for h in hits)
    assert hits[0].meta.get("fault") == "4"


def test_every_hit_carries_a_source_id(kb: Session) -> None:
    for h in search(kb, HashEmbedder(), "missing index sequential scan tasks", k=5):
        assert h.id and h.source
    assert kb.execute(select(KnowledgeChunk.id)).first() is not None


def test_resolved_incident_is_added_once(kb: Session) -> None:
    from opsloop.knowledge.ingest import add_resolved_incident

    emb = HashEmbedder()
    first = add_resolved_incident(kb, emb, incident_id=7, wing="api", summary="s", fix="f")
    second = add_resolved_incident(kb, emb, incident_id=7, wing="api", summary="s", fix="f")
    assert first is not None and second is None
    assert Path(SOURCES).exists()
