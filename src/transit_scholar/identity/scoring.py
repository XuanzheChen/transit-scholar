"""Deterministic scoring functions for paper-level duplicate detection.

All functions are pure and side-effect free: they only read from the
passed-in Paper objects and never write back to the database. Title
similarity uses RapidFuzz; author overlap uses normalised-name set
intersection.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from transit_scholar.db.models import Paper
from transit_scholar.metadata.normalizers import (
    normalize_author_name,
    normalize_title,
)


def calculate_author_overlap(authors_a: list[str], authors_b: list[str]) -> float:
    """Return the Jaccard-style overlap of two normalised-author sets.

    Defined as ``|intersection| / max(|a|, |b|)``. Returns ``0.0`` when
    either input list is empty so that a paper with no known authors can
    never boost a candidate into a higher-confidence bucket.
    """
    if not authors_a or not authors_b:
        return 0.0
    set_a = {normalize_author_name(n) for n in authors_a if normalize_author_name(n)}
    set_b = {normalize_author_name(n) for n in authors_b if normalize_author_name(n)}
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / max(len(set_a), len(set_b))


def calculate_title_similarity(
    title_a: str | None, title_b: str | None
) -> float:
    """Return a [0, 1] similarity score between two titre strings.

    Uses ``rapidfuzz.fuzz.token_set_ratio`` on the raw (pre-normalised)
    strings, divided by 100. ``None`` / empty inputs yield ``0.0``.
    """
    if not title_a or not title_b:
        return 0.0
    return fuzz.token_set_ratio(title_a, title_b) / 100.0


def _normalised_authors(paper: Paper) -> list[str]:
    """Collect non-empty normalised author names from a Paper's authors."""
    names: list[str] = []
    for a in paper.authors:
        n = a.normalized_name
        if n:
            names.append(n)
    return names


def _effective_normalized_title(paper: Paper) -> str | None:
    """Return the paper's normalised title, computing it if necessary.

    The computed value is returned to the caller but NEVER written back
    to the database — scoring is read-only.
    """
    if paper.normalized_title:
        return paper.normalized_title
    if paper.title:
        return normalize_title(paper.title)
    return None


def score_paper_pair(paper_a: Paper, paper_b: Paper) -> dict[str, Any]:
    """Deterministically score a pair of papers.

    Returns a dict with:
        - ``score``: float in [0, 1] (0.0 when nothing matches)
        - ``title_similarity``: float
        - ``author_overlap``: float
        - ``relation_type``: str | None (None if below weak threshold)
        - ``confidence``: float | None
        - ``reasons``: list[dict] (JSON-serialisable)
    """
    reasons: list[dict[str, Any]] = []

    # --- DOI exact match (highest priority) --------------------------------
    if paper_a.normalized_doi and paper_b.normalized_doi:
        if paper_a.normalized_doi == paper_b.normalized_doi:
            reasons.append({
                "type": "normalized_doi_match",
                "value": paper_a.normalized_doi,
            })
            return {
                "score": 1.0,
                "title_similarity": 0.0,
                "author_overlap": 0.0,
                "relation_type": "exact_duplicate",
                "confidence": 1.0,
                "reasons": reasons,
            }

    # --- arXiv ID exact match ----------------------------------------------
    if paper_a.arxiv_id and paper_b.arxiv_id:
        if paper_a.arxiv_id == paper_b.arxiv_id:
            reasons.append({
                "type": "arxiv_id_match",
                "value": paper_a.arxiv_id,
            })
            return {
                "score": 1.0,
                "title_similarity": 0.0,
                "author_overlap": 0.0,
                "relation_type": "exact_duplicate",
                "confidence": 1.0,
                "reasons": reasons,
            }

    # --- Title + author fuzzy scoring --------------------------------------
    title_a = _effective_normalized_title(paper_a)
    title_b = _effective_normalized_title(paper_b)
    title_sim = calculate_title_similarity(title_a, title_b)

    authors_a = _normalised_authors(paper_a)
    authors_b = _normalised_authors(paper_b)
    author_overlap = calculate_author_overlap(authors_a, authors_b)

    reasons.append({
        "type": "title_author_score",
        "title_similarity": round(title_sim, 4),
        "author_overlap": round(author_overlap, 4),
    })

    # --- Normalised-title exact match gets a confidence floor --------------
    if title_a and title_b and title_a == title_b:
        reasons.append({"type": "normalized_title_match"})
        score = max(0.95, 0.7 * title_sim + 0.3 * author_overlap)
        # Same title but no author overlap can never be probable_duplicate.
        if author_overlap < 0.5:
            # Cap below the probable bucket; let threshold logic decide.
            score = min(score, 0.94)
        else:
            return {
                "score": score,
                "title_similarity": title_sim,
                "author_overlap": author_overlap,
                "relation_type": "probable_duplicate",
                "confidence": score,
                "reasons": reasons,
            }

    score = 0.7 * title_sim + 0.3 * author_overlap
    reasons[-1]["score"] = round(score, 4)

    return {
        "score": score,
        "title_similarity": title_sim,
        "author_overlap": author_overlap,
        "relation_type": None,  # resolved by caller against thresholds
        "confidence": None,
        "reasons": reasons,
    }
