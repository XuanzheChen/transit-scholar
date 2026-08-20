"""Deterministic pure-Python Okapi BM25 (no external dependencies)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lower-cased alphanumeric tokenization used by BM25."""
    return _TOKEN_RE.findall(text.lower())


@dataclass
class BM25Index:
    """In-memory BM25 over a corpus of documents."""

    documents: list[str]
    tokenized: list[list[str]]
    doc_freq: dict[str, int]
    idf: dict[str, float]
    avgdl: float
    k1: float = 1.5
    b: float = 0.75

    @classmethod
    def build(cls, documents: Iterable[str], *, k1: float = 1.5, b: float = 0.75) -> "BM25Index":
        docs = list(documents)
        tokenized = [tokenize(doc) for doc in docs]
        doc_freq: dict[str, int] = {}
        total_len = 0
        for tokens in tokenized:
            total_len += len(tokens)
            for token in set(tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1
        doc_count = len(docs)
        idf: dict[str, float] = {}
        for token, df in doc_freq.items():
            idf[token] = math.log(
                1.0 + (doc_count - df + 0.5) / (df + 0.5)
            )
        avgdl = total_len / doc_count if doc_count else 0.0
        return cls(
            documents=docs,
            tokenized=tokenized,
            doc_freq=doc_freq,
            idf=idf,
            avgdl=avgdl,
            k1=k1,
            b=b,
        )

    def score(self, query: str, doc_index: int) -> float:
        tokens = tokenize(query)
        doc_tokens = self.tokenized[doc_index]
        doc_len = len(doc_tokens)
        freq: dict[str, int] = {}
        for token in doc_tokens:
            freq[token] = freq.get(token, 0) + 1
        score = 0.0
        for token in tokens:
            if token not in self.idf:
                continue
            tf = freq.get(token, 0)
            if tf == 0:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
            score += self.idf[token] * tf * (self.k1 + 1) / denom
        return score

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        scored: list[tuple[int, float]] = []
        for index in range(len(self.documents)):
            score = self.score(query, index)
            if score > 0:
                scored.append((index, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]
