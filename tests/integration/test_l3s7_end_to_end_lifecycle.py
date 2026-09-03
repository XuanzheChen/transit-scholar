from __future__ import annotations

import pytest

from transit_scholar.layer3.knowledge_evolution import (
    KnowledgeCandidate,
    KnowledgePromotionService,
)
from transit_scholar.layer3.memory import L3S7Lifecycle
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.runtime.run_runtime import RunResearchRuntime


class PromotionRole:
    def propose(self, normalized):
        return [
            KnowledgeCandidate(
                candidate_id="accepted",
                workspace_id=normalized["workspace_id"],
                originating_agent_run_id=normalized["agent_run_id"],
                title="Reusable transit finding",
                content="The admitted source supports the final finding.",
                source_claim_ids=("claim-1",),
                evidence_refs=("evidence-1",),
            ),
            KnowledgeCandidate(
                candidate_id="rejected",
                workspace_id=normalized["workspace_id"],
                originating_agent_run_id=normalized["agent_run_id"],
                title="Unproven finding",
                content="This must not become Wiki knowledge.",
                source_claim_ids=("unknown-claim",),
                evidence_refs=("evidence-1",),
            ),
        ]


class ExecutionService:
    def __init__(self):
        self.sessions = {}
        self.updated_runs = []

    def create_research_session(self, **kwargs):
        self.sessions[kwargs["research_session_id"]] = kwargs
        return kwargs

    def get_research_session(self, _agent_run_id, research_session_id):
        return self.sessions[research_session_id]

    def update_agent_run_status(self, agent_run_id, status):
        self.updated_runs.append((agent_run_id, status))
        return {
            "agent_run_id": agent_run_id,
            "workspace_id": "workspace-a",
            "user_goal": "Find transit evidence",
            "status": status,
        }


class SessionRuntime:
    def execute(self, **_kwargs):
        return {"status": "completed", "final_response": "Grounded answer"}


class Ledger:
    @staticmethod
    def list_queries(**_kwargs):
        return [
            {"query_id": "query-useful", "query_text": "transit source", "status": "completed"},
            {"query_id": "query-failed", "query_text": "missing source", "status": "failed"},
        ]

    @staticmethod
    def list_evidence(**_kwargs):
        return [
            {
                "evidence_id": "evidence-1",
                "source_query_id": "query-useful",
                "status": "admitted",
                "claim_ids": ("claim-1",),
                "paper_id": "paper-1",
            }
        ]

    @staticmethod
    def list_claims(**_kwargs):
        return [{"claim_id": "claim-1", "status": "supported", "statement": "Finding"}]


def test_completed_run_creates_one_episode_promotes_provenance_and_cleans_workspace():
    promotion = KnowledgePromotionService(PromotionRole())
    lifecycle = L3S7Lifecycle(promotion_service=promotion)
    decisions = iter(
        [
            RunDecision(mode="direct_session", proposed_questions=["Research transit sources"]),
            RunDecision(mode="direct_session", proposed_questions=["Research transit claims"]),
            RunDecision(mode="complete", completion_reason="sufficient evidence"),
        ]
    )
    execution = ExecutionService()
    runtime = RunResearchRuntime(
        session_runtime=SessionRuntime(),
        coordinator=lambda _snapshot: next(decisions),
        execution_service=execution,
        ledger_service=Ledger(),
        l3s7_lifecycle=lifecycle,
    )

    result = runtime.execute(
        agent_run_id="run-1",
        agent_run={
            "agent_run_id": "run-1",
            "workspace_id": "workspace-a",
            "user_goal": "Find transit evidence",
            "status": "running",
        },
    )

    assert result["status"] == "completed"
    assert execution.updated_runs == [("run-1", "completed")]
    episode = lifecycle.episodic_store.get_for_run(
        workspace_id="workspace-a", agent_run_id="run-1"
    )
    assert episode is not None
    assert episode.memory_id == "episodic-memory:run-1"
    assert set(episode.provenance.research_session_ids) == set(execution.sessions)
    assert len(episode.provenance.research_session_ids) == 2
    assert episode.useful_queries == ("transit source",)
    assert episode.failed_or_unhelpful_queries == ("missing source",)
    assert episode.is_authoritative_evidence is False
    assert len(promotion.retrieve("workspace-a")) == 1
    entry = promotion.retrieve("workspace-a")[0]["entry"]
    assert entry.source_claim_ids == ("claim-1",)
    assert entry.evidence_refs == ("evidence-1",)

    lifecycle.maintain_before_session(
        "workspace-a",
        claims=[{"claim_id": "claim-1", "status": "supported"}],
        evidence=[{"evidence_id": "evidence-1", "source_accessible": False}],
        papers=["paper-1"],
    )
    assert promotion.retrieve("workspace-a") == []
    assert promotion.retrieve("workspace-a", include_stale=True)[0]["entry"].status == "stale"

    with pytest.raises(PermissionError):
        lifecycle.episodic_store.get(episode.memory_id, workspace_id="workspace-b")
    with pytest.raises(PermissionError):
        promotion.get_entry(entry.entry_id, "workspace-b")

    class Layout:
        deleted = False

        def delete(self):
            self.deleted = True

    layout = Layout()
    lifecycle.workspace_file_cleanup("workspace-a", layout)
    assert lifecycle.episodic_store.list(workspace_id="workspace-a") == ()
    assert promotion.retrieve("workspace-a", include_stale=True) == []
    assert layout.deleted is True
