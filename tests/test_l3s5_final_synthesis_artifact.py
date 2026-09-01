"""Acceptance coverage for structured final synthesis and provenance propagation."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from transit_scholar.layer3.agent import FinalResponseArtifact, FinalSynthesisInput
from transit_scholar.layer3.evidence import EvidenceLocator
from transit_scholar.layer3.ledger import ClaimEvidenceLink, ClaimRecord, EvidenceRecord
from transit_scholar.layer3.roles import FinalSynthesisRole


def durable_state():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claim = ClaimRecord(
        claim_id="claim-1",
        research_session_id="session-1",
        statement="Signal priority reduces delay.",
        status="supported",
        created_at=now,
        updated_at=now,
    )
    evidence = EvidenceRecord(
        evidence_id="evidence-1",
        research_session_id="session-1",
        source_query_id="query-1",
        locator=EvidenceLocator(
            workspace_id="workspace-1",
            source_kind="paper",
            paper_id="paper-1",
            block_id="block-7",
            pages=[4],
        ),
        text_snapshot="Observed delay decreased after signal priority.",
        source_metadata={"title": "Transit Priority Study"},
        retrieval_provenance={"retriever": "hybrid", "rank": 1},
        created_at=now,
    )
    link = ClaimEvidenceLink(
        claim_id="claim-1",
        evidence_id="evidence-1",
        relation="supports",
        created_at=now,
    )
    return claim, evidence, link


def test_final_synthesis_returns_artifact_with_durable_source_provenance():
    claim, evidence, link = durable_state()
    role_input = FinalSynthesisInput(
        research_session_id="session-1",
        claims=(claim,),
        accepted_evidence=(evidence,),
        claim_evidence_links=(link,),
    )

    artifact = FinalSynthesisRole.finalize(
        role_input,
        {
            "completed": True,
            "answer_text": "Signal priority reduces transit delay [evidence-1].",
            "citation_references": ["evidence-1"],
        },
    )

    assert isinstance(artifact, FinalResponseArtifact)
    assert artifact.answer_text.startswith("Signal priority")
    assert artifact.citation_references == ["evidence-1"]
    assert artifact.source_references[0].evidence_id == "evidence-1"
    assert artifact.source_references[0].claim_ids == ("claim-1",)
    assert artifact.source_references[0].locator.block_id == "block-7"
    assert artifact.source_references[0].retrieval_provenance["retriever"] == "hybrid"


def test_final_synthesis_rejects_unknown_source_ids_before_presentation():
    claim, evidence, link = durable_state()
    role_input = FinalSynthesisInput(
        research_session_id="session-1",
        claims=(claim,),
        accepted_evidence=(evidence,),
        claim_evidence_links=(link,),
    )

    with pytest.raises(ValueError, match="unknown evidence"):
        FinalSynthesisRole.finalize(
            role_input,
            {
                "completed": True,
                "answer_text": "An unsupported answer.",
                "citation_references": ["invented-evidence"],
            },
        )


def test_unstructured_or_malformed_final_output_fails_schema_validation():
    with pytest.raises(ValidationError):
        FinalSynthesisRole.finalize(
            {"research_session_id": "session-1"},
            {"completed": True, "narrative": "Use evidence-1 and finish."},
        )


def test_final_role_projects_claim_evidence_links_and_has_narrow_allowlist():
    role = FinalSynthesisRole()

    assert "claim_evidence_links" in role.context_policy.included_sections
    assert role.allowed_actions == {"FINISH_SESSION"}
    assert role.allowed_tools == set()
