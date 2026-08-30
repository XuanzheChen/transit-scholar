"""Deterministic safety validation for LLM-proposed retrieval strategies."""

from __future__ import annotations

from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    RetrievalDiagnostic,
    RetrievalStrategy,
    SchemaRetrievalAction,
    WikiRetrievalAction,
)

from .models import RetrievalContext


class StrategyValidationError(ValueError):
    """A strategy violates current Workspace capabilities or configured bounds."""


def validate_strategy(strategy: RetrievalStrategy, context: RetrievalContext) -> None:
    """Reject unsafe actions after semantic planning and before execution."""
    capabilities = context.capabilities
    if strategy.query_id != context.query.query_id:
        raise StrategyValidationError("strategy query_id does not match the input query")
    if len(strategy.actions) > capabilities.max_actions:
        raise StrategyValidationError("strategy exceeds the configured action limit")

    actions_by_id = {action.action_id: action for action in strategy.actions}
    for action in strategy.actions:
        if action.source_kind not in capabilities.available_sources:
            raise StrategyValidationError(f"source_unavailable: {action.source_kind}")
        if not _tool_is_available(action.source_kind, capabilities.available_tools):
            raise StrategyValidationError(f"tool_unavailable: {action.source_kind}")
        if action.limit > capabilities.max_action_limit:
            raise StrategyValidationError("action_limit_exceeded")
        if isinstance(action, SchemaRetrievalAction):
            _validate_schema(action, context)
        elif isinstance(action, WikiRetrievalAction):
            if not capabilities.wiki_ready:
                raise StrategyValidationError("wiki_unready")
        elif isinstance(action, RagRetrievalAction):
            _validate_rag(action, context, actions_by_id)


def _validate_schema(action: SchemaRetrievalAction, context: RetrievalContext) -> None:
    capabilities = context.capabilities
    invalid_fields = set(action.field_ids) - capabilities.schema_field_ids
    if invalid_fields:
        raise StrategyValidationError(f"invalid_schema_field: {sorted(invalid_fields)!r}")
    _validate_papers(action.paper_ids, capabilities.eligible_paper_ids)
    unavailable = set(action.paper_ids) - capabilities.schema_ready_paper_ids
    if unavailable:
        raise StrategyValidationError(f"schema_unavailable_for_papers: {sorted(unavailable)!r}")


def _validate_rag(
    action: RagRetrievalAction,
    context: RetrievalContext,
    actions_by_id: dict[str, object],
) -> None:
    capabilities = context.capabilities
    if action.scope == "papers" and not action.paper_ids:
        discovery_actions = [actions_by_id[action_id] for action_id in action.depends_on]
        if not any(
            isinstance(dependency, WikiRetrievalAction)
            and dependency.discover_paper_ids
            for dependency in discovery_actions
        ):
            raise StrategyValidationError(
                "paper-scoped RAG without paper_ids requires a Wiki discovery dependency"
            )
        return
    paper_ids = (
        capabilities.eligible_paper_ids if action.scope == "workspace" else set(action.paper_ids)
    )
    _validate_papers(paper_ids, capabilities.eligible_paper_ids)
    unavailable = paper_ids - capabilities.l2s1_ready_paper_ids
    if unavailable:
        raise StrategyValidationError(f"rag_unavailable_for_papers: {sorted(unavailable)!r}")


def _validate_papers(paper_ids: set[str] | list[str], eligible_paper_ids: set[str]) -> None:
    outside = set(paper_ids) - eligible_paper_ids
    if outside:
        raise StrategyValidationError(f"paper_outside_workspace: {sorted(outside)!r}")


def _tool_is_available(source_kind: str, available_tools: set[str]) -> bool:
    """Accept source names or the public expert tool names in capabilities."""
    tool_names = {
        "schema": {"schema", "search_schema"},
        "wiki": {"wiki", "search_wiki"},
        "rag": {"rag", "search_rag", "search_workspace_rag"},
    }
    return bool(tool_names[source_kind] & available_tools)


def failure_diagnostic(error: Exception) -> RetrievalDiagnostic:
    """Produce the documented explicit safe fallback: no strategy is executed."""
    return RetrievalDiagnostic(
        code="planner_validation_failed",
        message=str(error) or "LLM planner returned an invalid strategy",
        status="failed",
    )
