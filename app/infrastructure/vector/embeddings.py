"""Embeddings.

**An embedding is content.** It is a lossy but very informative encoding of the
text it came from — enough to cluster it, retrieve it, and (with a decoder) partly
reconstruct it. So a private layer's embeddings get exactly the same treatment as
its notes:

* never written to a shared vector store;
* never persisted in the clear (they live in memory while unlocked, and go into
  encrypted objects if persistence is ever turned on);
* never sent to a remote embedding provider unless the layer's AI policy allows it.

Milestone 4 ships the abstraction plus a deterministic local embedder, so that
semantic search, clustering and the hybrid ranker are real and testable end to end
without a model download. A real local model (Ollama, llama.cpp) and remote
providers arrive in Milestone 7 behind the same interface — the *policy* around
them is what matters, and it is enforced here rather than at the call site.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Matrix = NDArray[np.float32]

_WORD = re.compile(r"[\w'-]+", re.UNICODE)

DEFAULT_DIMENSIONS = 256


@dataclass(frozen=True)
class Embedding:
    object_id: str
    layer_id: str
    vector: Matrix

    def __post_init__(self) -> None:
        if self.vector.ndim != 1:
            raise ValueError("An embedding must be a 1-D vector.")


class Embedder(ABC):
    """Turns text into a vector. Implementations are swapped, not subclassed into."""

    model_id: str
    dimensions: int
    is_local: bool
    # How many query terms this embedder needs before its similarity means anything.
    # A trained model: 1. A hashed bag-of-words: more, because a one-word query is a
    # single hash bucket and every collision looks like a match.
    min_query_terms: int = 1

    @abstractmethod
    def embed(self, texts: list[str]) -> Matrix:
        """Return an (n, dimensions) matrix of L2-normalised row vectors."""


class HashingEmbedder(Embedder):
    """A deterministic local embedder: hashed bag-of-words with sublinear TF.

    This is not a language model, and it is not pretending to be one. It captures
    lexical overlap, so it will find a paraphrase that reuses words and will *not*
    find an unrelated wording of the same idea. That is the honest limit of the
    technique, and the search service enforces it (`min_query_terms`) rather than
    dressing hash collisions up as insight.

    What it does give us today: a real vector space, real cosine similarity, real
    clustering, and a hybrid ranker that can be tested end to end. Swapping in a
    real model in Milestone 7 changes the vectors, not the pipeline.

    It runs locally and deterministically, so it is safe for any layer — including a
    private one with AI disabled, because nothing leaves the process.
    """

    model_id = "strata-hashing-v1"
    is_local = True
    min_query_terms = 2

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def _bucket(self, term: str) -> tuple[int, float]:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % self.dimensions
        # A signed hash keeps unrelated terms from piling up in one direction.
        sign = 1.0 if digest[4] & 1 else -1.0
        return index, sign

    def embed(self, texts: list[str]) -> Matrix:
        matrix: Matrix = np.zeros((len(texts), self.dimensions), dtype=np.float32)

        for row, text in enumerate(texts):
            counts: dict[str, int] = {}
            for match in _WORD.finditer(text.lower()):
                term = match.group(0)
                counts[term] = counts.get(term, 0) + 1

            for term, count in counts.items():
                index, sign = self._bucket(term)
                # Sublinear term frequency: the tenth occurrence of a word says much
                # less than the second.
                matrix[row, index] += sign * (1.0 + math.log(count))

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normalised: Matrix = (matrix / norms).astype(np.float32)
        return normalised


class VectorStore:
    """An in-memory vector index for one layer.

    In memory *only*. For a private layer that is the entire point (see the module
    docstring); for a public layer it is a Milestone 4 simplification, and the
    persistence path is an encrypted object either way.
    """

    def __init__(self, layer_id: str, dimensions: int) -> None:
        self.layer_id = layer_id
        self.dimensions = dimensions
        self._ids: list[str] = []
        self._matrix: Matrix = np.zeros((0, dimensions), dtype=np.float32)

    def replace(self, object_ids: list[str], vectors: Matrix) -> None:
        if vectors.shape[0] != len(object_ids):
            raise ValueError("Vector count does not match id count.")
        self._ids = list(object_ids)
        self._matrix = vectors.astype(np.float32, copy=True)

    def search(self, query: Matrix, limit: int) -> list[tuple[str, float]]:
        if self._matrix.shape[0] == 0:
            return []
        # Rows are L2-normalised, so a dot product is the cosine similarity.
        scores = self._matrix @ query.astype(np.float32)
        order = np.argsort(-scores)[:limit]
        return [(self._ids[int(i)], float(scores[int(i)])) for i in order]

    def vector_for(self, object_id: str) -> Matrix | None:
        try:
            index = self._ids.index(object_id)
        except ValueError:
            return None
        vector: Matrix = self._matrix[index]
        return vector

    def cluster(self, count: int, iterations: int = 12, seed: int = 7) -> dict[str, int]:
        """k-means over the layer's vectors. Returns object id to cluster index.

        Deterministic (fixed seed, deterministic init): a graph whose clusters
        reshuffle every time you open it is not a map of anything.
        """
        n = int(self._matrix.shape[0])
        if n == 0 or count <= 0:
            return {}
        count = min(count, n)

        rng = np.random.default_rng(seed)
        centroids = self._matrix[rng.choice(n, size=count, replace=False)].copy()

        assignments = np.zeros(n, dtype=np.int32)
        for _ in range(iterations):
            distances = self._matrix @ centroids.T
            new_assignments = np.argmax(distances, axis=1).astype(np.int32)
            if np.array_equal(new_assignments, assignments):
                break
            assignments = new_assignments
            for index in range(count):
                members = self._matrix[assignments == index]
                if members.shape[0] == 0:
                    continue
                centroid = members.mean(axis=0)
                norm = float(np.linalg.norm(centroid))
                centroids[index] = centroid / norm if norm else centroid

        return {object_id: int(assignments[i]) for i, object_id in enumerate(self._ids)}

    def close(self) -> None:
        self._ids = []
        self._matrix = np.zeros((0, self.dimensions), dtype=np.float32)

    def size(self) -> int:
        return len(self._ids)
