from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance, AgenticWikiStore
from transit_scholar.layer3.knowledge_evolution.models import AgenticWikiEntry
from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ClaimEvidenceLinkService,
    ClaimService,
    EvidenceService,
    QueryService,
    ResearchReasoningLedgerService,
)
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.db.models import Paper


def entry(**kw):
    data = dict(entry_id="e1", workspace_id="w1", title="T", content="C",
                originating_agent_run_id="r1", source_claim_ids=("c1",), evidence_refs=("ev1",))
    data.update(kw)
    return AgenticWikiEntry(**data)


def test_missing_paper_marks_stale_without_delete():
    store = AgenticWikiStore(); store.put(entry())
    changed = AgenticWikiMaintenance(store, claims=["c1"], evidence=[{"id": "ev1", "paper_id": "p1"}], papers=[])("w1")
    assert changed and store.get("e1", "w1").status == "stale"


def test_unresolvable_provenance_and_rejected_claim_stale():
    store = AgenticWikiStore(); store.put(entry())
    AgenticWikiMaintenance(store, claims=[{"id": "c1", "status": "rejected"}], evidence=[{"id": "ev1", "provenance_resolvable": False}])("w1")
    assert store.get("e1", "w1").status == "stale"


def test_invalid_version_and_supersession_stale_and_isolation():
    store = AgenticWikiStore(); store.put(entry(evidence_refs=("ev1",)))
    AgenticWikiMaintenance(store, evidence=[{"id": "ev1", "source_version_valid": False}])("w1")
    assert store.get("e1", "w1").status == "stale"
    store.put(entry(entry_id="e2", workspace_id="w2"))
    assert [e.entry_id for e in store.list("w1")] == []


def test_production_composition_reads_authoritative_workspace_and_ledger(
    session, project_tmp_path
):
    paper = Paper(title="Authoritative maintenance paper", status="active")
    session.add(paper)
    session.flush()
    workspace = WorkspaceService(session).create(name="Maintenance authority").workspace
    workspace_service = WorkspaceService(session)
    workspace_service.add_paper(workspace.workspace_id, paper.id)
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Check provenance"
    )
    research_session = AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="Check provenance"
    )
    query = QueryService(session).create_query(
        research_session_id=research_session.research_session_id,
        query_text="source",
        status="completed",
    )
    evidence = EvidenceService(session).admit_evidence(
        research_session_id=research_session.research_session_id,
        source_query_id=query.query_id,
        evidence=ResearchEvidence(
            evidence_id="retrieved-evidence",
            locator=EvidenceLocator(
                workspace_id=workspace.workspace_id,
                source_kind="paper",
                paper_id=paper.id,
            ),
            text="Grounded source text",
            source_kind="paper",
        ),
    )
    claim = ClaimService(session).create_claim(
        research_session_id=research_session.research_session_id,
        statement="The source is available.",
        status="supported",
    )
    ClaimEvidenceLinkService(session).link_evidence_to_claim(
        research_session_id=research_session.research_session_id,
        claim_id=claim.claim_id,
        evidence_id=evidence.evidence_id,
        relation="supports",
    )
    store = AgenticWikiStore.for_workspace(
        workspace.workspace_id, base_dir=project_tmp_path
    )
    store.put(
        entry(
            workspace_id=workspace.workspace_id,
            source_claim_ids=(claim.claim_id,),
            evidence_refs=(evidence.evidence_id,),
        )
    )
    maintenance = AgenticWikiMaintenance.for_workspace(
        workspace.workspace_id,
        base_dir=project_tmp_path,
        workspace_service=workspace_service,
        ledger_service=ResearchReasoningLedgerService(session),
    )
    assert maintenance(workspace.workspace_id) == []
    workspace_service.remove_paper(workspace.workspace_id, paper.id)
    assert maintenance(workspace.workspace_id)
    assert maintenance.store.get("e1", workspace.workspace_id).status == "stale"
