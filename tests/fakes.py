"""Test doubles shared across unit and integration tests."""
from __future__ import annotations

import hashlib
import math
import re


class HashingEmbedder:
    """Deterministic stand-in for a sentence-transformer: a hashed bag of words,
    unit length. Texts sharing words score higher, which is enough to test
    indexing and ranking without downloading a model."""

    model_name = "hashing-test-v1"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        vectors = []
        for text in texts:
            vector = [0.0] * self.dim
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vectors.append([x / norm for x in vector])
        return vectors
