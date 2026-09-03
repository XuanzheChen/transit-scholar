"""Run-bound orchestration for the Layer3 episodic and semantic memory flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transit_scholar.layer3.knowledge_evolution import (
    KnowledgeCandidate,
    KnowledgePromotionRole,
    KnowledgePromotionService,
    PromotionInput,
)
from transit_scholar.layer3.agentic_wiki import AgenticWikiStore
from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance

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


def _materialize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        identity_keys = {"id", "query_id", "evidence_id", "claim_id"}
        return [value] if identity_keys.intersection(value) else list(value.values())
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


@dataclass(frozen=True, slots=True)
class RunMemoryLifecycleResult:
    """Artifacts produced exactly once for a completed AgentRun."""

    episode: EpisodicMemoryRecord
    candidates: tuple[KnowledgeCandidate, ...]


class L3S7Lifecycle:
    """Coordinate bounded run-end memory evolution without owning research state."""

    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        *,
        base_dir: str | None = None,
        data_root: str | None = None,
        semantic_provider: Any | None = None,
        workspace_service: Any | None = None,
        ledger_service: Any | None = None,
        execution_service: Any | None = None,
    ) -> "L3S7Lifecycle":
        """Construct the durable, provider-required production composition."""
        if base_dir is not None and data_root is not None:
            raise ValueError("supply exactly one of base_dir or data_root")
        return cls(
            workspace_id=workspace_id,
            base_dir=base_dir,
            data_root=data_root,
            semantic_provider=semantic_provider,
            workspace_service=workspace_service,
            ledger_service=ledger_service,
            execution_service=execution_service,
        )

    production = for_workspace

    def __init__(
        self,
        *,
        episodic_store: EpisodicMemoryStore | None = None,
        promotion_service: KnowledgePromotionService | None = None,
        collector: EpisodicMemoryCollector | None = None,
        distiller: EpisodicMemoryDistiller | None = None,
        workspace_id: str | None = None,
        base_dir: str | None = None,
        data_root: str | None = None,
        semantic_provider: Any | None = None,
        workspace_service: Any | None = None,
        ledger_service: Any | None = None,
        execution_service: Any | None = None,
        maintenance: AgenticWikiMaintenance | None = None,
    ) -> None:
        if base_dir is not None and data_root is not None:
            raise ValueError("supply exactly one of base_dir or data_root")
        if data_root is not None:
            from ..storage.paths import workspace_layout

            base_dir = str(workspace_layout("_l3s7", data_root=data_root).base_dir)
        self._base_dir = base_dir
        self._workspace_id = workspace_id
        self._episodic_store_explicit = episodic_store is not None
        self._promotion_service_explicit = promotion_service is not None
        self._production_default = (
            workspace_id is not None
            and episodic_store is None
            and distiller is None
        )
        self._semantic_provider = semantic_provider
        self.workspace_service = workspace_service
        self.ledger_service = ledger_service
        self.execution_service = execution_service
        self.maintenance = maintenance
        self._maintenance_explicit = maintenance is not None
        self.episodic_store = episodic_store or (
            EpisodicMemoryStore.for_workspace(workspace_id, base_dir=base_dir)
            if workspace_id is not None else EpisodicMemoryStore()
        )
        self.promotion_service = promotion_service
        episodic_workspace = getattr(self.episodic_store, "bound_workspace_id", None)
        if workspace_id is not None and episodic_workspace not in (None, workspace_id):
            raise PermissionError("lifecycle is bound to another Workspace")
        promotion_workspace = getattr(promotion_service, "workspace_id", None)
        if workspace_id is not None and promotion_workspace not in (None, workspace_id):
            raise PermissionError("lifecycle is bound to another Workspace")
        self.collector = collector or EpisodicMemoryCollector()
        self.distiller = distiller or (
            EpisodicMemoryDistiller.production(semantic_provider)
            if self._production_default else EpisodicMemoryDistiller()
        )
        if workspace_id is not None and self.promotion_service is None:
            self._compose_production_services(workspace_id)

    def _compose_production_services(self, workspace_id: str) -> None:
        """Open Workspace-bound durable repositories for the default path."""
        if self._production_default and not self._episodic_store_explicit:
            if self._workspace_id is not None and self._workspace_id != workspace_id:
                raise PermissionError("lifecycle is bound to another Workspace")
            if self.episodic_store.bound_workspace_id != workspace_id:
                self.episodic_store = EpisodicMemoryStore.for_workspace(
                    workspace_id, base_dir=self._base_dir
                )
            self._workspace_id = workspace_id
        if self.promotion_service is None:
            if self._workspace_id is not None and self._workspace_id != workspace_id:
                raise PermissionError("lifecycle is bound to another Workspace")
            role = (
                KnowledgePromotionRole.production(self._semantic_provider)
                if self._production_default
                else KnowledgePromotionRole()
            )
            wiki_store = (
                AgenticWikiStore.for_workspace(
                    workspace_id, base_dir=self._base_dir
                )
                if self._production_default
                else AgenticWikiStore()
            )
            self.promotion_service = KnowledgePromotionService(role=role, store=wiki_store)
            self._workspace_id = workspace_id
        self._compose_maintenance(workspace_id)

    def _compose_maintenance(self, workspace_id: str) -> None:
        if self.maintenance is not None:
            return
        workspace_service = self.workspace_service
        if workspace_service is None and self.execution_service is not None:
            workspace_service = getattr(self.execution_service, "workspaces", None)
        ledger_service = self.ledger_service
        if ledger_service is None:
            db_session = getattr(workspace_service, "session", None) or getattr(
                self.execution_service, "session", None
            )
            if db_session is not None:
                try:
                    from transit_scholar.layer3.ledger import ResearchReasoningLedgerService

                    ledger_service = ResearchReasoningLedgerService(db_session)
                    self.ledger_service = ledger_service
                except (ImportError, TypeError):
                    ledger_service = None
        self.maintenance = AgenticWikiMaintenance(
            self.promotion_service.store,
            workspace_service=workspace_service,
            ledger_service=ledger_service,
            execution_service=self.execution_service,
        )

    def configure_authoritative_readers(
        self,
        *,
        workspace_service: Any | None = None,
        ledger_service: Any | None = None,
        execution_service: Any | None = None,
    ) -> None:
        """Attach production Workspace/L3S4 readers to Session-start maintenance."""
        if workspace_service is not None:
            self.workspace_service = workspace_service
        if ledger_service is not None:
            self.ledger_service = ledger_service
        if execution_service is not None:
            self.execution_service = execution_service
        if self.maintenance is not None and self._maintenance_explicit:
            if workspace_service is not None:
                self.maintenance.workspace_service = workspace_service
            if ledger_service is not None:
                self.maintenance.ledger_service = ledger_service
                self.maintenance.session = getattr(ledger_service, "session", None)
            if execution_service is not None:
                self.maintenance.execution_service = execution_service
        else:
            self.maintenance = None

    def complete_agent_run(
        self,
        *,
        agent_run: Any,
        research_sessions: Any | None = None,
        queries: Any = (),
        evidence: Any = (),
        claims: Any = (),
        claim_evidence_links: Any = None,
        final_outcome: str | None = None,
        semantic_output: EpisodicSemanticOutput | None = None,
        promotion_input: PromotionInput | None = None,
    ) -> RunMemoryLifecycleResult:
        """Persist one auxiliary episode and execute one promotion cycle."""
        status = _value(agent_run, "status")
        if status is not None and status != "completed":
            raise ValueError("L3S7 lifecycle requires a completed AgentRun")
        query_values = _materialize_records(queries)
        evidence_values = _materialize_records(evidence)
        claim_values = _materialize_records(claims)
        if not query_values:
            query_values = _materialize_records(_value(agent_run, "queries"))
        if not evidence_values:
            evidence_values = _materialize_records(_value(agent_run, "evidence"))
        if not claim_values:
            claim_values = _materialize_records(_value(agent_run, "claims"))
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
            queries=query_values,
            evidence=evidence_values,
            claims=claim_values,
            final_outcome=final_outcome,
        )
        self._compose_production_services(normalized.workspace_id)
        episode = self.episodic_store.get_for_run(
            workspace_id=normalized.workspace_id,
            agent_run_id=normalized.agent_run_id,
        )
        if episode is None:
            semantic = semantic_output or self.distiller.distill(normalized)
            episode = build_episodic_record(
                normalized,
                semantic,
                claims=claim_values,
            )
            self.episodic_store.put(episode)
        if promotion_input is None:
            promotion_input = PromotionInput(
                workspace_id=normalized.workspace_id,
                agent_run_id=normalized.agent_run_id,
                claims=claim_values,
                evidence=evidence_values,
                claim_evidence_links=(
                    None if claim_evidence_links is None else list(claim_evidence_links)
                ),
                research_session_ids=list(normalized.session_ids),
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
        self._compose_production_services(workspace_id)
        if self.maintenance is None:
            self._compose_maintenance(workspace_id)
        if resolvers:
            return self.maintenance(workspace_id, **resolvers)
        return self.maintenance(workspace_id)

    def delete_workspace(self, workspace_id: str) -> None:
        """Remove only this Workspace's L3S7-owned long-term memory artifacts."""
        self.episodic_store.delete_workspace(workspace_id)
        if self.promotion_service is not None:
            self.promotion_service.store.delete_workspace(workspace_id)

    def workspace_file_cleanup(self, workspace_id: str, layout: Any) -> None:
        """WorkspaceService.delete callback preserving the existing derived-storage cleanup."""
        self.delete_workspace(workspace_id)
        layout.delete()


__all__ = ["L3S7Lifecycle", "RunMemoryLifecycleResult"]
