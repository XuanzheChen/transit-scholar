import json

from transit_scholar.api.schemas import AnswerEvidenceCitationResponse, CitationResponse
from transit_scholar.db.models import (
    AgentRun, EvidenceRecord, Paper, ResearchQueryRecord, ResearchSession, Workspace,
)
from transit_scholar.product.facade import TransitScholarProduct


def test_completed_run_answer_citations_resolve_admitted_evidence(session):
    workspace = Workspace(id="ws-cite", name="Citation workspace", status="active", schema_mode="none", revision=1)
    paper = Paper(id="paper-cite", title="A Transit Study", status="active")
    run = AgentRun(id="run-cite", workspace_id=workspace.id, user_goal="Explain transit", status="completed", workspace_revision=1)
    research_session = ResearchSession(id="session-cite", agent_run_id=run.id, research_question="Transit", status="completed")
    query = ResearchQueryRecord(id="query-cite", research_session_id=research_session.id, query_text="Transit", status="completed")
    locator = {
        "workspace_id": workspace.id, "source_kind": "paper", "paper_id": paper.id,
        "parse_run_id": "parse-1", "canonical_source_version": "parse-1",
        "block_id": "block-7", "pages": [4], "span": {"start": 10, "end": 28},
    }
    evidence = EvidenceRecord(
        id="evidence-cite", research_session_id=research_session.id,
        source_query_id=query.id, locator_json=json.dumps(locator),
        text_snapshot="Admitted supporting quote.",
        source_metadata_json=json.dumps({"source_kind": "paper", "paper_provenance": {"title": paper.title}}),
        retrieval_provenance_json="{}",
    )
    session.add_all([workspace, paper, run, research_session, query, evidence])
    session.flush()

    product = TransitScholarProduct(session, runtime_factory=None)
    citations = product.answer_citations(run.id, {
        "answer_text": "Transit answer", "citation_references": [evidence.id],
    })
    assert citations == [{
        "evidence_id": evidence.id, "research_session_id": research_session.id,
        "paper_id": paper.id, "paper_title": paper.title, "source_kind": "paper",
        "pages": [4], "block_id": "block-7", "character_start": 10,
        "character_end": 28, "parse_run_id": "parse-1",
        "canonical_source_version": "parse-1", "evidence_quote": evidence.text_snapshot,
    }]


def test_bibliography_and_answer_citations_are_distinct_dtos():
    assert CitationResponse is not AnswerEvidenceCitationResponse
    assert "raw_text" in CitationResponse.model_fields
    assert "evidence_id" in AnswerEvidenceCitationResponse.model_fields
    assert "raw_text" not in AnswerEvidenceCitationResponse.model_fields
