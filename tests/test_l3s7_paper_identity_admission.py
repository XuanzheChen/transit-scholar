import pytest

from transit_scholar.layer3.evidence import (
    EvidenceLocator,
    PaperProvenance,
    QueryProvenance,
    ResearchEvidence,
)
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import EvidenceService, InvalidEvidenceInputError, QueryService
from transit_scholar.layer3.workspace import WorkspaceService


def _context(session):
    workspace = WorkspaceService(session).create(name="identity-admission").workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="identity"
    )
    research_session = AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="identity"
    )
    query = QueryService(session).create_query(
        research_session_id=research_session.research_session_id,
        query_text="identity",
    )
    return workspace.workspace_id, research_session.research_session_id, query.query_id


def _evidence(workspace_id, query_id, *, locator_identity=None, provenance_identity=None):
    return ResearchEvidence(
        evidence_id="evidence-identity",
        locator=EvidenceLocator(
            workspace_id=workspace_id,
            source_kind="paper",
            paper_id="paper-1",
            parse_run_id=locator_identity,
        ),
        text="paper text",
        source_kind="paper",
        query_provenance=QueryProvenance(query_id=query_id),
        paper_provenance=PaperProvenance(
            paper_id="paper-1", parse_run_id=provenance_identity
        ) if provenance_identity is not None else None,
    )


def test_identity_less_paper_evidence_is_rejected(session):
    workspace_id, research_session_id, query_id = _context(session)
    with pytest.raises(InvalidEvidenceInputError, match="stable source identity"):
        EvidenceService(session).admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query_id,
            evidence=_evidence(workspace_id, query_id),
        )


def test_conflicting_paper_source_identities_are_rejected(session):
    workspace_id, research_session_id, query_id = _context(session)
    with pytest.raises(InvalidEvidenceInputError, match="identities conflict"):
        EvidenceService(session).admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query_id,
            evidence=_evidence(
                workspace_id,
                query_id,
                locator_identity="parse-a",
                provenance_identity="parse-b",
            ),
        )


@pytest.mark.parametrize("target", ["locator", "provenance"])
def test_conflicting_source_identity_aliases_are_rejected(session, target):
    workspace_id, research_session_id, query_id = _context(session)
    evidence = _evidence(workspace_id, query_id, locator_identity="parse-a", provenance_identity="parse-a")
    if target == "locator":
        evidence = evidence.model_copy(update={
            "locator": evidence.locator.model_copy(
                update={"canonical_source_version": "parse-b"}
            )
        })
    else:
        evidence = evidence.model_copy(update={
            "paper_provenance": evidence.paper_provenance.model_copy(
                update={"canonical_source_version": "parse-b"}
            )
        })
    with pytest.raises(InvalidEvidenceInputError, match="source identities conflict"):
        EvidenceService(session).admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query_id,
            evidence=evidence,
        )
