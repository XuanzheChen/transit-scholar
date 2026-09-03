from transit_scholar.layer3.knowledge_evolution import (
    AgenticWikiEntry,
    KnowledgeCandidate,
    KnowledgePromotionService,
    PromotionInput,
)
from transit_scholar.layer3.runtime.run_runtime import RunResearchRuntime


def _input(workspace_id="workspace-1", run_id="run-1"):
    return PromotionInput(
        workspace_id=workspace_id,
        agent_run_id=run_id,
        claims=[
            {"claim_id": "claim-1", "status": "supported", "statement": "First fact"},
            {"claim_id": "claim-2", "status": "supported", "statement": "Second fact"},
            {"claim_id": "claim-rejected", "status": "rejected", "statement": "Obsolete"},
        ],
        evidence=[
            {"evidence_id": "evidence-1", "status": "admitted", "provenance": "paper-1"},
            {"evidence_id": "evidence-2", "status": "admitted", "provenance": "paper-2"},
            {"evidence_id": "evidence-rejected", "status": "admitted", "provenance": "paper-3"},
        ],
        claim_evidence_links=[
            {"claim_id": "claim-1", "evidence_id": "evidence-1"},
            {"claim_id": "claim-2", "evidence_id": "evidence-2"},
            {"claim_id": "claim-rejected", "evidence_id": "evidence-rejected"},
        ],
    )


class CapturingRole:
    def __init__(self, candidates=None):
        self.calls = []
        self.candidates = candidates

    def propose(self, normalized):
        self.calls.append(normalized)
        if self.candidates is not None:
            return self.candidates
        return [KnowledgeCandidate(
            candidate_id="candidate-1",
            workspace_id=normalized["workspace_id"],
            originating_agent_run_id=normalized["agent_run_id"],
            title="Combined finding",
            content="First and second facts synthesized.",
            source_claim_ids=("claim-1", "claim-2"),
            evidence_refs=("evidence-1", "evidence-2"),
        )]


def _candidate(**updates):
    values = {
        "candidate_id": "candidate-1",
        "workspace_id": "workspace-1",
        "originating_agent_run_id": "run-1",
        "title": "Finding",
        "content": "Content",
        "source_claim_ids": ("claim-1",),
        "evidence_refs": ("evidence-1",),
    }
    values.update(updates)
    return KnowledgeCandidate(**values)


def test_completed_run_gets_one_workspace_scoped_end_cycle_and_rejected_claims_are_excluded():
    role = CapturingRole()
    service = KnowledgePromotionService(role)

    first = service.run_end(_input())
    repeated = service.run_end(_input())
    other_workspace = service.run_end(_input(workspace_id="workspace-2"))

    assert [candidate.status for candidate in first] == ["accepted"]
    assert repeated == []
    assert [candidate.status for candidate in other_workspace] == ["accepted"]
    assert len(role.calls) == 2
    assert {claim["claim_id"] for claim in role.calls[0]["claims"]} == {"claim-1", "claim-2"}
    assert {item["evidence_id"] for item in role.calls[0]["evidence"]} == {"evidence-1", "evidence-2"}
    assert len(service.entries) == 2


def test_non_completed_run_does_not_consume_promotion_cycle():
    role = CapturingRole()
    service = KnowledgePromotionService(role)
    pending = _input()
    pending.agent_run_status = "running"

    try:
        service.run_end(pending)
    except ValueError as error:
        assert "completed" in str(error)
    else:
        raise AssertionError("running AgentRun should not be promoted")

    assert service.run_end(_input())[0].status == "accepted"
    assert len(role.calls) == 1


def test_existing_workspace_wiki_summaries_reach_semantic_role_and_update_is_supported():
    role = CapturingRole([_candidate(proposed_target_entry_id="entry-1")])
    service = KnowledgePromotionService(role)
    service.store.put(AgenticWikiEntry(
        entry_id="entry-1",
        workspace_id="workspace-1",
        title="Old title",
        content="Old content",
        source_claim_ids=("old-claim",),
        evidence_refs=("old-evidence",),
        originating_agent_run_id="old-run",
    ))

    result = service.run_end(_input())

    assert result[0].status == "accepted"
    assert role.calls[0]["wiki_summaries"][0]["entry_id"] == "entry-1"
    assert service.entries["entry-1"].content == "Content"
    assert service.entries["entry-1"].originating_agent_run_id == "run-1"


def test_unknown_empty_or_inconsistent_provenance_is_rejected_without_wiki_mutation():
    candidates = [
        _candidate(candidate_id="empty", source_claim_ids=(), evidence_refs=()),
        _candidate(candidate_id="unknown-claim", source_claim_ids=("invented",)),
        _candidate(candidate_id="unknown-evidence", evidence_refs=("invented",)),
        _candidate(candidate_id="wrong-link", source_claim_ids=("claim-1",), evidence_refs=("evidence-2",)),
        _candidate(candidate_id="wrong-workspace", workspace_id="workspace-2"),
        _candidate(candidate_id="wrong-run", originating_agent_run_id="run-2"),
    ]
    service = KnowledgePromotionService(CapturingRole(candidates))

    result = service.run_end(_input())

    assert all(candidate.status == "rejected" for candidate in result)
    assert service.entries == {}


def test_run_runtime_collects_links_from_var_keyword_facade():
    class VarKeywordFacade:
        def __init__(self):
            self.calls = []

        def get_claim_evidence(self, **kwargs):
            self.calls.append(kwargs)
            return [{"claim_id": kwargs["claim_id"], "evidence_id": "e-1"}]

    facade = VarKeywordFacade()
    runtime = object.__new__(RunResearchRuntime)
    runtime.ledger_service = facade
    links = runtime._collect_claim_evidence_links(
        "session-1", [{"claim_id": "claim-1"}, {"claim_id": "claim-2"}]
    )

    assert [call["research_session_id"] for call in facade.calls] == ["session-1", "session-1"]
    assert [link["claim_id"] for link in links] == ["claim-1", "claim-2"]


def test_inaccessible_or_broken_evidence_is_not_eligible_for_promotion():
    promotion_input = _input()
    promotion_input.evidence[0]["source_accessible"] = False
    promotion_input.evidence[1]["provenance_resolvable"] = False
    service = KnowledgePromotionService(CapturingRole([_candidate()]))

    result = service.run_end(promotion_input)

    assert result[0].status == "rejected"
    assert service.entries == {}
