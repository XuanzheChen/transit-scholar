"""Layer3 Stage3 ResearchEvidence contract tests."""

from __future__ import annotations

from transit_scholar.layer3.evidence import ResearchEvidence


def test_research_evidence_round_trips_locator_and_retrieval_provenance():
    evidence = ResearchEvidence(
        evidence_id="evidence-1",
        query_provenance={
            "query_id": "query-1",
            "session_id": "session-1",
            "query_text": "Find sources",
        },
        locator={
            "workspace_id": "workspace-1",
            "source_kind": "paper",
            "paper_id": "paper-1",
            "block_id": "block-1",
            "pages": [7],
        },
        text="The evidence text.",
        source_kind="rag",
        paper_provenance={"paper_id": "paper-1", "title": "Paper One"},
        section="Results",
        retrieval_provenance={"action_id": "rag", "provider": "l2s1"},
        rerank_provenance={"provider": "ranker", "candidate_id": "candidate-1"},
        final_rank=1,
    )

    restored = ResearchEvidence.model_validate_json(evidence.model_dump_json())

    assert restored == evidence
    assert restored.locator.paper_id == "paper-1"
    assert restored.final_rank == 1


def test_research_evidence_is_claim_free():
    fields = ResearchEvidence.model_fields

    assert {"claim_status", "claim_confidence", "claim_text", "conclusion"}.isdisjoint(
        fields
    )
