"""Hybrid retrieval: pgvector cosine similarity + PostgreSQL full-text rank, fused with
reciprocal rank fusion. Retrieved text is DATA: callers quote it with its source id and never
treat it as instructions."""

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from opsloop.core.embedder import Embedder

RRF_K = 60
_STOP = frozenset(
    "the a an and or of to in on for is are was were be by with from at as it this that".split()
)


@dataclass(frozen=True)
class Hit:
    id: int
    source: str
    kind: str
    wing: str | None
    title: str
    text: str
    score: float
    meta: dict[str, Any]


def _or_query(query: str) -> str:
    tokens = [t for t in dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower())) if t not in _STOP]
    return " | ".join(tokens[:40])


def search(
    session: Session,
    embedder: Embedder,
    query: str,
    *,
    k: int = 5,
    wing: str | None = None,
    kinds: list[str] | None = None,
    pool: int = 30,
) -> list[Hit]:
    vec = "[" + ",".join(f"{x:.6f}" for x in embedder.embed([query])[0]) + "]"
    sql = text(
        """
        WITH sem AS (
            SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:vec AS vector)) AS rn
            FROM ops.knowledge_chunks
            WHERE (CAST(:wing AS text) IS NULL OR wing = :wing OR wing IS NULL)
              AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[])))
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :pool
        ), kw AS (
            SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rn
            FROM ops.knowledge_chunks, to_tsquery('english', :tsq) AS q
            WHERE :tsq <> '' AND tsv @@ q
              AND (CAST(:wing AS text) IS NULL OR wing = :wing OR wing IS NULL)
              AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[])))
            ORDER BY ts_rank_cd(tsv, q) DESC
            LIMIT :pool
        )
        SELECT c.id, c.source, c.kind, c.wing, c.title, c.text, c.meta,
               COALESCE(1.0 / (:rrf + sem.rn), 0) + COALESCE(1.0 / (:rrf + kw.rn), 0) AS score
        FROM ops.knowledge_chunks c
        LEFT JOIN sem ON sem.id = c.id
        LEFT JOIN kw ON kw.id = c.id
        WHERE sem.id IS NOT NULL OR kw.id IS NOT NULL
        ORDER BY score DESC, c.id
        LIMIT :k
        """
    )
    rows = session.execute(
        sql,
        {
            "vec": vec,
            "wing": wing,
            "kinds": kinds,
            "tsq": _or_query(query),
            "pool": pool,
            "rrf": RRF_K,
            "k": k,
        },
    ).all()
    return [
        Hit(r.id, r.source, r.kind, r.wing, r.title, r.text, float(r.score), r.meta) for r in rows
    ]
