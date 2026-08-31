"""Framework-neutral handlers for query-level knowledge retrieval tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from transit_scholar.layer3.evidence import (
    EvidenceLocator,
    PaperProvenance,
    QueryProvenance,
    ResearchEvidence,
)
from transit_scholar.layer3.planner import (
    HybridKnowledgeRetrievalPlanner,
    RetrievalContext,
    assemble_retrieval_context,
)
from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    ResearchQuery,
    RetrievalDiagnostic,
    RetrievalStrategy,
    SchemaResult,
    SchemaRetrievalAction,
    WikiNavigationResult,
    WikiRetrievalAction,
    WorkspaceRagRetriever,
)
from transit_scholar.layer3.workspace.errors import WorkspaceChangedError

from .contracts import RetrievalResultEnvelope


class WorkspaceKnowledgeAccess(Protocol):
    """The workspace-bound reads consumed by tool handlers."""

    workspace_id: str

    def current_state(self) -> Any: ...

    def list_papers(self) -> list[Any]: ...

    def get_schema_instance(self, paper_id: str) -> Any: ...

    def search_wiki(self, query: str, *, limit: int, mode: str) -> Any: ...

    def search_evidence(self, paper_id: str, query: str, *, top_k: int) -> Any: ...

    def resolve_wiki_hit_paper_ids(self, hit: Any) -> list[str]: ...


class KnowledgeToolService:
    """Thin bindings over workspace-safe knowledge operations.

    The service deliberately owns no provider, registry, permission, or agent
    runtime integration.  Direct expert methods do not consult the planner.
    """

    def __init__(
        self,
        gateway: WorkspaceKnowledgeAccess,
        *,
        planner: HybridKnowledgeRetrievalPlanner | None = None,
        context_factory: Callable[[ResearchQuery], RetrievalContext] | None = None,
        workspace_rag_retriever: WorkspaceRagRetriever | None = None,
    ) -> None:
        self.gateway = gateway
        self.planner = planner
        self.context_factory = context_factory
        self.workspace_rag_retriever = workspace_rag_retriever

    def retrieve_knowledge(
        self,
        query: ResearchQuery,
        *,
        context: RetrievalContext | None = None,
    ) -> RetrievalResultEnvelope:
        """Plan and execute retrieval for one already-formed research query."""
        self._verify_query(query)
        if self.planner is None:
            raise RuntimeError("retrieve_knowledge requires an injected retrieval planner")
        expected_revision = self.gateway.current_state().revision
        requested_context = context
        if requested_context is None and self.context_factory is not None:
            requested_context = self._build_context(query)
        resolved_context = assemble_retrieval_context(
            query,
            self.gateway,
            requested=requested_context,
            available_tools=self._available_tools(),
        )
        self._require_revision(expected_revision)
        planned = self.planner.plan(resolved_context)
        self._require_revision(expected_revision)
        if not planned.is_valid or planned.strategy is None:
            return RetrievalResultEnvelope(query=query, diagnostics=planned.diagnostics)
        return self._execute_strategy(
            query,
            planned.strategy,
            planned.diagnostics,
            expected_revision=expected_revision,
        )

    def search_schema(
        self, query: ResearchQuery, action: SchemaRetrievalAction
    ) -> RetrievalResultEnvelope:
        """Execute direct structured Schema retrieval without planning."""
        self._verify_query(query)
        results: list[SchemaResult] = []
        paper_views = self.gateway.list_papers()
        workspace_paper_ids = [paper.paper_id for paper in paper_views]
        if action.paper_ids:
            paper_ids = action.paper_ids
        elif any(hasattr(paper, "schema_status") for paper in paper_views):
            paper_ids = [
                paper.paper_id
                for paper in paper_views
                if getattr(paper, "schema_status", None) == "ready"
            ]
        else:
            # Lightweight composition gateways predating schema readiness
            # metadata retain their existing all-member behavior.
            paper_ids = workspace_paper_ids
        self._require_workspace_papers(paper_ids, workspace_paper_ids)
        for paper_id in paper_ids:
            instance = self.gateway.get_schema_instance(paper_id)
            field_ids = action.field_ids or sorted(instance.fields)
            for field_id in field_ids:
                if field_id not in instance.fields:
                    continue
                field = instance.fields[field_id]
                results.append(
                    SchemaResult(
                        action_id=action.action_id,
                        paper_id=paper_id,
                        field_id=field_id,
                        value=field.value,
                        provenance={
                            "status": field.status,
                            "confidence": field.confidence,
                            "notes": field.notes,
                            "evidence": [
                                evidence.model_dump(mode="json")
                                for evidence in field.evidence
                            ],
                        },
                    )
                )
                if len(results) >= action.limit:
                    return RetrievalResultEnvelope(query=query, schema_results=results)
        return RetrievalResultEnvelope(query=query, schema_results=results)

    def search_wiki(
        self, query: ResearchQuery, action: WikiRetrievalAction
    ) -> RetrievalResultEnvelope:
        """Execute direct Wiki navigation/discovery without planning."""
        self._verify_query(query)
        result = self.gateway.search_wiki(
            action.source_query, limit=action.limit, mode=action.mode
        )
        resolver = getattr(self.gateway, "resolve_wiki_hit_paper_ids", None)
        wiki_results = []
        for hit in result.hits:
            discovered_paper_ids = (
                resolver(hit)
                if action.discover_paper_ids and callable(resolver)
                else []
            )
            wiki_results.append(
                WikiNavigationResult(
                    action_id=action.action_id,
                    node_id=hit.object_id,
                    title=hit.title,
                    discovered_paper_ids=discovered_paper_ids,
                    navigation={
                        "type": hit.type,
                        "snippet": hit.snippet,
                        "retrieval_mode": hit.retrieval_mode,
                        "score": hit.score,
                    },
                )
            )
        diagnostics = self._result_diagnostics(action.action_id, result)
        return RetrievalResultEnvelope(
            query=query, wiki_results=wiki_results, diagnostics=diagnostics
        )

    def search_rag(
        self, query: ResearchQuery, action: RagRetrievalAction
    ) -> RetrievalResultEnvelope:
        """Execute direct Paper-scoped source-grounded RAG without planning."""
        if action.scope != "papers":
            raise ValueError("search_rag requires a paper-scoped RAG action")
        if not action.paper_ids:
            raise ValueError(
                "direct search_rag requires resolved paper_ids; use the unified "
                "strategy path for Wiki discovery dependencies"
            )
        return self._search_rag(query, action, action.paper_ids)

    def search_workspace_rag(
        self, query: ResearchQuery, action: RagRetrievalAction
    ) -> RetrievalResultEnvelope:
        """Execute direct Workspace-wide source-grounded RAG without planning."""
        if action.scope != "workspace":
            raise ValueError("search_workspace_rag requires a workspace-scoped RAG action")
        if self.workspace_rag_retriever is not None:
            self._verify_query(query)
            result = self.workspace_rag_retriever.retrieve(
                query,
                source_query=action.source_query,
                action_id=action.action_id,
                top_k=action.limit,
            )
            return RetrievalResultEnvelope(
                query=query,
                evidence_results=result.evidence_results,
                diagnostics=result.diagnostics,
                workspace_revision=result.workspace_revision,
                searched_paper_ids=result.searched_paper_ids,
                skipped_paper_ids=result.skipped_paper_ids,
                unavailable_paper_ids=result.unavailable_paper_ids,
                failed_paper_ids=result.failed_paper_ids,
                rerank_diagnostics=result.rerank_diagnostics,
            )
        raise RuntimeError(
            "search_workspace_rag requires an injected semantic "
            "WorkspaceRagRetriever/CrossPaperRanker"
        )

    def inspect_evidence(self, evidence: ResearchEvidence) -> ResearchEvidence:
        """Return one evidence item after rechecking its workspace boundary."""
        self.gateway.current_state()
        if evidence.locator.workspace_id != self.gateway.workspace_id:
            raise ValueError("evidence belongs to a different workspace")
        if evidence.locator.source_kind.casefold() == "paper":
            paper_id = evidence.locator.paper_id
            if not paper_id:
                raise ValueError("Paper-backed evidence is missing paper_id")
            get_paper = getattr(self.gateway, "get_paper", None)
            if callable(get_paper):
                get_paper(paper_id)
            else:
                self._require_workspace_papers([paper_id])
        return evidence

    def _execute_strategy(
        self,
        query: ResearchQuery,
        strategy: RetrievalStrategy,
        diagnostics: list[RetrievalDiagnostic],
        *,
        expected_revision: int,
    ) -> RetrievalResultEnvelope:
        envelope = RetrievalResultEnvelope(
            query=query, strategy=strategy, diagnostics=list(diagnostics)
        )
        discovered_by_action: dict[str, list[str]] = {}
        for action in strategy.actions:
            self._require_revision(expected_revision)
            if isinstance(action, SchemaRetrievalAction):
                result = self.search_schema(query, action)
            elif isinstance(action, WikiRetrievalAction):
                result = self.search_wiki(query, action)
                discovered_by_action[action.action_id] = sorted(
                    {
                        paper_id
                        for item in result.wiki_results
                        for paper_id in item.discovered_paper_ids
                    }
                )
            elif action.scope == "papers":
                resolved_action = action
                if not action.paper_ids:
                    paper_ids = sorted(
                        {
                            paper_id
                            for dependency in action.depends_on
                            for paper_id in discovered_by_action.get(dependency, [])
                        }
                    )
                    if not paper_ids:
                        envelope.diagnostics.append(
                            RetrievalDiagnostic(
                                action_id=action.action_id,
                                code="wiki_discovery_empty",
                                message="Wiki discovery returned no eligible Papers",
                                status="skipped",
                            )
                        )
                        continue
                    resolved_action = action.model_copy(
                        update={"paper_ids": paper_ids}
                    )
                result = self.search_rag(query, resolved_action)
            else:
                result = self.search_workspace_rag(query, action)
            envelope.schema_results.extend(result.schema_results)
            envelope.wiki_results.extend(result.wiki_results)
            envelope.evidence_results.extend(result.evidence_results)
            envelope.diagnostics.extend(result.diagnostics)
            if result.workspace_revision is not None:
                envelope.workspace_revision = result.workspace_revision
            envelope.searched_paper_ids.extend(result.searched_paper_ids)
            envelope.skipped_paper_ids.extend(result.skipped_paper_ids)
            envelope.unavailable_paper_ids.extend(result.unavailable_paper_ids)
            envelope.failed_paper_ids.extend(result.failed_paper_ids)
            if result.rerank_diagnostics is not None:
                envelope.rerank_diagnostics = result.rerank_diagnostics
            self._require_revision(expected_revision)
        self._require_revision(expected_revision)
        envelope.workspace_revision = expected_revision
        return envelope

    def _search_rag(
        self,
        query: ResearchQuery,
        action: RagRetrievalAction,
        paper_ids: list[str],
    ) -> RetrievalResultEnvelope:
        self._verify_query(query)
        self._require_workspace_papers(paper_ids)
        evidence_results: list[ResearchEvidence] = []
        diagnostics: list[RetrievalDiagnostic] = []
        for paper_id in paper_ids:
            result = self.gateway.search_evidence(
                paper_id, action.source_query, top_k=action.limit
            )
            if result.status != "ok":
                diagnostics.extend(self._result_diagnostics(action.action_id, result, paper_id))
                continue
            for hit in result.hits:
                source_ref = hit.source_refs[0] if hit.source_refs else None
                block_id = source_ref.block_id if source_ref else hit.chunk_id
                evidence_results.append(
                    ResearchEvidence(
                        evidence_id=(
                            f"{action.action_id}:{paper_id}:{block_id or 'hit'}:{hit.rank}"
                        ),
                        locator=EvidenceLocator(
                            workspace_id=query.workspace_id,
                            source_kind="paper",
                            paper_id=paper_id,
                            block_id=block_id,
                            pages=hit.pages,
                            span=(
                                {"start": source_ref.char_start, "end": source_ref.char_end}
                                if source_ref
                                else None
                            ),
                        ),
                        text=hit.text,
                        source_kind="rag",
                        query_provenance=QueryProvenance(
                            query_id=query.query_id,
                            session_id=query.session_id,
                            query_text=query.query_text,
                        ),
                        paper_provenance=PaperProvenance(paper_id=paper_id),
                        section=" / ".join(hit.section_path) or None,
                        retrieval_provenance={
                            "action_id": action.action_id,
                            "method": hit.retrieval_method,
                            "local_rank": hit.rank,
                            "local_score": hit.score,
                        },
                    )
                )
        return RetrievalResultEnvelope(
            query=query, evidence_results=evidence_results, diagnostics=diagnostics
        )

    def _build_context(self, query: ResearchQuery) -> RetrievalContext:
        if self.context_factory is None:  # pragma: no cover - caller guards this
            raise RuntimeError("retrieval context factory is not configured")
        return self.context_factory(query)

    def _available_tools(self) -> set[str]:
        tools = {"search_schema", "search_wiki", "search_rag"}
        if self.workspace_rag_retriever is not None:
            tools.add("search_workspace_rag")
        return tools

    def _require_revision(self, expected_revision: int) -> None:
        current_revision = self.gateway.current_state().revision
        if current_revision != expected_revision:
            raise WorkspaceChangedError(
                "workspace changed during unified retrieval; partial results were discarded",
                expected_revision=expected_revision,
                current_revision=current_revision,
            )

    def _verify_query(self, query: ResearchQuery) -> None:
        if query.workspace_id != self.gateway.workspace_id:
            raise ValueError("query belongs to a different workspace")
        self.gateway.current_state()

    def _workspace_paper_ids(self) -> list[str]:
        return [paper.paper_id for paper in self.gateway.list_papers()]

    def _require_workspace_papers(
        self, paper_ids: list[str], workspace_paper_ids: list[str] | None = None
    ) -> None:
        if workspace_paper_ids is None:
            workspace_paper_ids = self._workspace_paper_ids()
        available_paper_ids = set(workspace_paper_ids)
        unavailable_paper_ids = sorted(set(paper_ids) - available_paper_ids)
        if unavailable_paper_ids:
            raise ValueError(
                "paper_ids are not members of the current workspace: "
                f"{unavailable_paper_ids!r}"
            )

    @staticmethod
    def _result_diagnostics(
        action_id: str, result: Any, paper_id: str | None = None
    ) -> list[RetrievalDiagnostic]:
        if getattr(result, "status", "ok") == "ok":
            return []
        details = {"error_code": getattr(result, "error_code", None)}
        if paper_id is not None:
            details["paper_id"] = paper_id
        return [
            RetrievalDiagnostic(
                action_id=action_id,
                code=getattr(result, "error_code", None) or "source_unavailable",
                message=getattr(result, "error_message", None) or "source unavailable",
                status="degraded",
                details=details,
            )
        ]


__all__ = ["KnowledgeToolService", "WorkspaceKnowledgeAccess"]
