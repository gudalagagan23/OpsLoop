"""Embedding provider interface (local by default, so runs are free and reproducible)."""

import hashlib
import math
import re
from typing import Protocol

from opsloop.core.config import Settings, get_settings


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Local ONNX embeddings via fastembed. The model is downloaded once, then runs offline."""

    def __init__(self, model_name: str, dim: int) -> None:
        from fastembed import TextEmbedding

        self.dim = dim
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = [list(map(float, v)) for v in self._model.embed(texts)]
        if out and len(out[0]) != self.dim:
            raise ValueError(f"embedding model returned {len(out[0])} dims, expected {self.dim}")
        return out


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. TEST DOUBLE: lexical overlap only, no
    semantics. Used so retrieval plumbing can be tested without downloading a model."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9_]+", text.lower()):
                h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big")
                vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def get_embedder(settings: Settings | None = None) -> Embedder:
    s = settings or get_settings()
    if s.embedding_provider == "hash":
        return HashEmbedder(s.embedding_dim)
    if s.embedding_provider == "fastembed":
        if not s.embedding_model:
            raise ValueError("set OPSLOOP_EMBEDDING_MODEL in .env (see .env.example)")
        return FastEmbedder(s.embedding_model, s.embedding_dim)
    raise ValueError(f"unknown embedding provider {s.embedding_provider!r}")
