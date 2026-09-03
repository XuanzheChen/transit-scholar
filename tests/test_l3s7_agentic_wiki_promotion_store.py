from transit_scholar.layer3.knowledge_evolution import (
    AgenticWikiEntry, KnowledgeCandidate, KnowledgePromotionService, PromotionInput,
)


class Role:
    def __init__(self, candidate): self.candidate = candidate
    def propose(self, normalized): return [self.candidate]


def source_input():
    return PromotionInput(
        workspace_id="ws", agent_run_id="run", claims=[{"claim_id": "c", "status": "supported", "statement": "Fact"}],
        evidence=[{"evidence_id": "ev", "status": "admitted", "provenance": "paper"}],
        claim_evidence_links=[{"claim_id": "c", "evidence_id": "ev"}],
    )


def candidate(**kwargs):
    values = dict(candidate_id="cand", workspace_id="ws", originating_agent_run_id="run", title="T", content="C", source_claim_ids=("c",), evidence_refs=("ev",))
    values.update(kwargs)
    return KnowledgeCandidate(**values)


def test_create_and_update_existing_entry_without_duplicate():
    service = KnowledgePromotionService(Role(candidate()))
    assert service.run_end(source_input())[0].status == "accepted"
    assert len(service.entries) == 1
    existing = next(iter(service.entries.values()))
    service2 = KnowledgePromotionService(Role(candidate(proposed_target_entry_id=existing.entry_id, content="Updated")), service.store)
    result = service2.run_end(source_input())
    assert result[0].status == "accepted"
    assert len(service2.entries) == 1
    assert service2.get_entry(existing.entry_id, "ws").content == "Updated"


def test_rejected_candidate_performs_no_write():
    service = KnowledgePromotionService(Role(candidate(source_claim_ids=("unknown",))))
    result = service.run_end(source_input())
    assert result[0].status == "rejected"
    assert service.entries == {}
