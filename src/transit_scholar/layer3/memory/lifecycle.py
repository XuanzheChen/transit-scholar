"""Run-bound orchestration for the Layer3 episodic and semantic memory flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transit_scholar.layer3.knowledge_evolution import (
    KnowledgeCandidate,
    KnowledgePromotionService,
    PromotionInput,
)

from .episodic import (
    EpisodicMemoryCollector,
    EpisodicMemoryDistiller,
    EpisodicSemanticOutput,
    build_episodic_record,
)
from .models import EpisodicMemoryRecord
from .retrieval import EpisodicMemoryStore


def _value(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, dict) else getattr(source, name, default)


@dataclass(frozen=True, slots=True)
class RunMemoryLifecycleResult:
    """Artifacts produced exactly once for a completed AgentRun."""

    episode: EpisodicMemoryRecord
    candidates: tuple[KnowledgeCandidate, ...]


class L3S7Lifecycle:
    """Coordinate bounded run-end memory evolution without owning research state."""

    def __init__(
        self,
        *,
        episodic_store: EpisodicMemoryStore | None = None,
        promotion_service: KnowledgePromotionService | None = None,
        collector: EpisodicMemoryCollector | None = None,
        distiller: EpisodicMemoryDistiller | None = None,
    ) -> None:
        self.episodic_store = episodic_store or EpisodicMemoryStore()
        self.promotion_service = promotion_service or KnowledgePromotionService()
        self.collector = collector or EpisodicMemoryCollector()
        self.distiller = distiller or EpisodicMemoryDistiller()

    def complete_agent_run(
        self,
        *,
        agent_run: Any,
        research_sessions: Any | None = None,
        queries: Any = (),
        evidence: Any = (),
        claims: Any = (),
        claim_evidence_links: Any = (),
        final_outcome: str | None = None,
        semantic_output: EpisodicSemanticOutput | None = None,
        promotion_input: PromotionInput | None = None,
    ) -> RunMemoryLifecycleResult:
        """Persist one auxiliary episode and execute one promotion cycle."""
        status = _value(agent_run, "status")
        if status is not None and status != "completed":
            raise ValueError("L3S7 lifecycle requires a completed AgentRun")
        episode_source = agent_run
        if research_sessions is not None:
            source_data = (
                agent_run.model_dump(mode="python")
                if hasattr(agent_run, "model_dump")
                else dict(agent_run)
            )
            source_data["sessions"] = research_sessions
            episode_source = source_data
        normalized = self.collector.collect(
            episode_source,
            queries=queries,
            evidence=evidence,
            claims=claims,
            final_outcome=final_outcome,
        )
        episode = self.episodic_store.get_for_run(
            workspace_id=normalized.workspace_id,
            agent_run_id=normalized.agent_run_id,
        )
        if episode is None:
            semantic = semantic_output or self.distiller.distill(normalized)
            episode = build_episodic_record(
                normalized,
                semantic,
                claims=claims,
            )
            self.episodic_store.put(episode)
        if promotion_input is None:
            promotion_input = PromotionInput(
                workspace_id=normalized.workspace_id,
                agent_run_id=normalized.agent_run_id,
                claims=list(claims),
                evidence=list(evidence),
                claim_evidence_links=list(claim_evidence_links),
                agent_run_status="completed",
            )
        if (
            promotion_input.workspace_id != normalized.workspace_id
            or promotion_input.agent_run_id != normalized.agent_run_id
        ):
            raise PermissionError("promotion input belongs to another Workspace or AgentRun")
        candidates = self.promotion_service.run_end(promotion_input)
        return RunMemoryLifecycleResult(episode=episode, candidates=tuple(candidates))

    def maintain_before_session(self, workspace_id: str, **resolvers: Any) -> list[Any]:
        """Run deterministic Agentic Wiki provenance maintenance for one Workspace."""
        return self.promotion_service.maintain(workspace_id, **resolvers)

    def delete_workspace(self, workspace_id: str) -> None:
        """Remove only this Workspace's L3S7-owned long-term memory artifacts."""
        self.episodic_store.delete_workspace(workspace_id)
        self.promotion_service.store.delete_workspace(workspace_id)

    def workspace_file_cleanup(self, workspace_id: str, layout: Any) -> None:
        """WorkspaceService.delete callback preserving the existing derived-storage cleanup."""
        self.delete_workspace(workspace_id)
        layout.delete()


__all__ = ["L3S7Lifecycle", "RunMemoryLifecycleResult"]
