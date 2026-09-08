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
    unrelated_run = AgentRun(
        id="run-unrelated", workspace_id=workspace.id, user_goal="Other question",
        status="completed", workspace_revision=1,
    )
    unrelated_session = ResearchSession(
        id="session-unrelated", agent_run_id=unrelated_run.id,
        research_question="Other question", status="completed",
    )
    unrelated_query = ResearchQueryRecord(
        id="query-unrelated", research_session_id=unrelated_session.id,
        query_text="Other query", status="completed",
    )
    unrelated_evidence = EvidenceRecord(
        id="evidence-unrelated", research_session_id=unrelated_session.id,
        source_query_id=unrelated_query.id, locator_json="{}",
        text_snapshot="Evidence from a different run.", source_metadata_json="{}",
        retrieval_provenance_json="{}",
    )
    session.add_all([
        workspace, paper, run, research_session, query, evidence, unrelated_run,
        unrelated_session, unrelated_query, unrelated_evidence,
    ])
    session.flush()

    product = TransitScholarProduct(session, runtime_factory=None)
    citations = product.answer_citations(run.id, {
        "answer_text": "Transit answer",
        "citation_references": [unrelated_evidence.id, evidence.id],
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


def test_answer_citation_uses_persisted_paper_provenance_when_locator_is_sparse(session):
    workspace = Workspace(id="ws-sparse", name="Sparse citation workspace", status="active", schema_mode="none", revision=1)
    paper = Paper(id="paper-sparse", title="Sparse Paper", status="active")
    run = AgentRun(id="run-sparse", workspace_id=workspace.id, user_goal="Explain", status="completed", workspace_revision=1)
    research_session = ResearchSession(id="session-sparse", agent_run_id=run.id, research_question="Explain", status="completed")
    query = ResearchQueryRecord(id="query-sparse", research_session_id=research_session.id, query_text="Explain", status="completed")
    evidence = EvidenceRecord(
        id="evidence-sparse", research_session_id=research_session.id, source_query_id=query.id,
        locator_json=json.dumps({"source_kind": "paper", "pages": [2]}),
        text_snapshot="Quote", source_metadata_json=json.dumps({
            "paper_provenance": {"paper_id": paper.id, "title": paper.title, "parse_run_id": "parse-2"},
        }), retrieval_provenance_json="{}",
    )
    session.add_all([workspace, paper, run, research_session, query, evidence])
    session.flush()

    citations = TransitScholarProduct(session, runtime_factory=None).answer_citations(
        run.id, {"citation_references": [evidence.id]}
    )
    assert citations[0]["paper_id"] == paper.id
    assert citations[0]["parse_run_id"] == "parse-2"
