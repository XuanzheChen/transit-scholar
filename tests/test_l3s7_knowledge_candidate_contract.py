from transit_scholar.layer3.knowledge_evolution import KnowledgeCandidate


def test_knowledge_candidate_serializes_and_reloads_all_contract_fields():
    candidate = KnowledgeCandidate(
        candidate_id="candidate-1",
        workspace_id="workspace-1",
        originating_agent_run_id="run-1",
        title="Reusable finding",
        content="A durable synthesis.",
        source_claim_ids=("claim-1", "claim-2"),
        evidence_refs=("evidence-1", "evidence-2"),
        proposed_target_entry_id="entry-1",
        status="accepted",
    )

    reloaded = KnowledgeCandidate.model_validate_json(candidate.model_dump_json())

    assert reloaded == candidate
    assert reloaded.status == "accepted"


def test_knowledge_candidate_supports_all_lifecycle_statuses():
    base = {
        "candidate_id": "candidate-1",
        "workspace_id": "workspace-1",
        "originating_agent_run_id": "run-1",
        "title": "Finding",
        "content": "Content",
        "source_claim_ids": ["claim-1"],
        "evidence_refs": ["evidence-1"],
    }

    assert {KnowledgeCandidate(**base, status=status).status for status in ("proposed", "accepted", "rejected")} == {
        "proposed",
        "accepted",
        "rejected",
    }
