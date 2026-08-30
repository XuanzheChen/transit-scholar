"""Ranking-only prompt construction for LLM evidence reranking."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer3.retrieval.models import ResearchQuery
    from transit_scholar.layer3.retrieval.workspace_rag import CrossPaperCandidate


def build_evidence_rerank_prompt(
    query: "ResearchQuery", candidates: Sequence["CrossPaperCandidate"]
) -> str:
    """Request a complete evidence-ID ordering without answer generation."""
    evidence_items = "\n".join(
        f"- candidate_id: {candidate.candidate_id!r}; paper_id: {candidate.paper_id!r}; "
        f"evidence: {candidate.evidence.text!r}"
        for candidate in candidates
    )
    return (
        "Rank the supplied evidence candidates for usefulness to the already-formed "
        "research query. Evaluate directness, evidentiary value, and specificity. "
        "This is evidence ranking only: do not answer the research question, do not "
        "write a conclusion, and do not generate or request Claims. Return only a JSON "
        "object with one key, ranked_candidate_ids, whose value is every supplied stable "
        "candidate_id exactly once, ordered best to worst.\n"
        f"Research query: {query.query_text!r}\n"
        f"Evidence candidates:\n{evidence_items}"
    )


__all__ = ["build_evidence_rerank_prompt"]
