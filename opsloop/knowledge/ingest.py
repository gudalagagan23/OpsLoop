"""Knowledge ingestion: runbooks, architecture docs, known-incident scenarios.

Held-out faults (spec section 16) must never reach the knowledge base; ingestion refuses them.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opsloop.core.embedder import Embedder
from opsloop.core.models import KnowledgeChunk

SOURCES = Path(__file__).parent / "sources"
HELD_OUT_FAULTS = frozenset({"5", "9", "11"})

_WING_FILES = {"api", "database", "auth", "billing", "infra", "ux"}


class HeldOutFault(Exception):
    pass


@dataclass(frozen=True)
class Chunk:
    source: str
    kind: str
    wing: str | None
    title: str
    text: str
    meta: dict[str, Any]

    @property
    def content_hash(self) -> str:
        key = f"{self.source}\n{self.kind}\n{self.title}\n{self.text}"
        return hashlib.sha256(key.encode()).hexdigest()


def _split_markdown(source: str, kind: str, wing: str | None, text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    doc_title = ""
    current_title = ""
    body: list[str] = []

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            title = f"{doc_title}: {current_title}" if current_title else doc_title
            chunks.append(Chunk(source, kind, wing, title, f"{title}\n{content}", {}))

    for line in text.splitlines():
        if line.startswith("# "):
            doc_title = line[2:].strip()
        elif line.startswith("## "):
            flush()
            current_title, body = line[3:].strip(), []
        else:
            body.append(line)
    flush()
    return chunks


def incident_chunk(source: str, data: dict[str, Any]) -> Chunk:
    fault = str(data["fault"])
    if fault in HELD_OUT_FAULTS:
        raise HeldOutFault(f"fault {fault} is held out of the knowledge base ({source})")
    text = (
        f"Known incident: {data['title']}\n"
        f"Symptoms: {data['symptoms']}\n"
        f"Signals: {', '.join(data.get('signals', []))}\n"
        f"Root cause: {data['root_cause']}\n"
        f"Fix: {data['fix']}\n"
        f"Validation: {data['validation']}"
    )
    return Chunk(
        source,
        "known_incident",
        data.get("wing"),
        data["title"],
        text,
        {"fault": fault, "signals": data.get("signals", [])},
    )


def collect_chunks(root: Path = SOURCES) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted((root / "runbooks").glob("*.md")):
        wing = path.stem if path.stem in _WING_FILES else None
        chunks += _split_markdown(f"runbooks/{path.name}", "runbook", wing, path.read_text())
    for path in sorted((root / "architecture").glob("*.md")):
        chunks += _split_markdown(
            f"architecture/{path.name}", "architecture", None, path.read_text()
        )
    for path in sorted((root / "incidents").glob("*.yaml")):
        chunks.append(incident_chunk(f"incidents/{path.name}", yaml.safe_load(path.read_text())))
    return chunks


def ingest(
    session: Session, embedder: Embedder, chunks: list[Chunk] | None = None
) -> dict[str, int]:
    """Make the knowledge base match `chunks` for the sources they cover. Idempotent."""
    chunks = collect_chunks() if chunks is None else chunks
    sources = {c.source for c in chunks}
    wanted = {c.content_hash: c for c in chunks}
    existing = {
        h: i
        for i, h in session.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.content_hash).where(
                KnowledgeChunk.source.in_(sources)
            )
        )
    }
    stale = [i for h, i in existing.items() if h not in wanted]
    if stale:
        session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id.in_(stale)))
    new = [c for h, c in wanted.items() if h not in existing]
    if new:
        vectors = embedder.embed([c.text for c in new])
        for c, vec in zip(new, vectors, strict=True):
            session.add(
                KnowledgeChunk(
                    source=c.source,
                    kind=c.kind,
                    wing=c.wing,
                    title=c.title,
                    text=c.text,
                    embedding=vec,
                    meta=c.meta,
                    content_hash=c.content_hash,
                )
            )
    session.flush()
    return {"added": len(new), "removed": len(stale), "kept": len(wanted) - len(new)}


def add_resolved_incident(
    session: Session, embedder: Embedder, *, incident_id: int, wing: str, summary: str, fix: str
) -> KnowledgeChunk | None:
    """Past incident + validated fix. Callers must only use this after a RESOLVED verdict."""
    text = f"Past incident INC-{incident_id:05d} ({wing}): {summary}\nValidated fix: {fix}"
    c = Chunk(f"past_incident/{incident_id}", "past_incident", wing, summary[:200], text, {})
    if session.execute(
        select(KnowledgeChunk.id).where(KnowledgeChunk.content_hash == c.content_hash)
    ).first():
        return None
    row = KnowledgeChunk(
        source=c.source,
        kind=c.kind,
        wing=wing,
        title=c.title,
        text=c.text,
        embedding=embedder.embed([c.text])[0],
        meta={"incident_id": incident_id},
        content_hash=c.content_hash,
    )
    session.add(row)
    session.flush()
    return row


def main() -> None:
    from opsloop.core.db import get_engine
    from opsloop.core.embedder import get_embedder

    with Session(get_engine()) as s:
        print(ingest(s, get_embedder()))
        s.commit()


if __name__ == "__main__":
    main()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())
