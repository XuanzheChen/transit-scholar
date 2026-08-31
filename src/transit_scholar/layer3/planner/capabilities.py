"""Authoritative assembly of bounded retrieval capabilities."""

from __future__ import annotations

from typing import Any

from transit_scholar.layer3.retrieval import ResearchQuery

from .models import RetrievalCapabilities, RetrievalContext


DEFAULT_TOOLS = {
    "search_schema",
    "search_wiki",
    "search_rag",
    "search_workspace_rag",
}


def assemble_retrieval_context(
    query: ResearchQuery,
    gateway: Any,
    *,
    requested: RetrievalContext | None = None,
    available_tools: set[str] | None = None,
    max_actions: int = 8,
    max_action_limit: int = 20,
) -> RetrievalContext:
    """Build capabilities from current Workspace state, then apply restrictions.

    ``requested`` can narrow production capabilities and limits, but it cannot
    add Papers, readiness, fields, tools, or limits absent from authoritative
    state.  Lightweight test/composition gateways without ``wiki_status`` may
    supply Wiki readiness explicitly; the production gateway always exposes
    the authoritative status method.
    """
    if query.workspace_id != gateway.workspace_id:
        raise ValueError("query belongs to a different workspace")
    if requested is not None and requested.query != query:
        raise ValueError("retrieval context query must match the requested query")

    state = gateway.current_state()
    papers = list(gateway.list_papers())
    eligible_paper_ids = {paper.paper_id for paper in papers}
    l2s1_ready_paper_ids = {
        paper.paper_id for paper in papers if getattr(paper, "l2s1_ready", False)
    }

    schema_ready_paper_ids: set[str] = set()
    schema_field_ids: set[str] = set()
    get_schema_instance = getattr(gateway, "get_schema_instance", None)
    requested_schema_papers = (
        requested.capabilities.schema_ready_paper_ids if requested is not None else set()
    )
    for paper in papers:
        schema_status = getattr(paper, "schema_status", None)
        should_probe = schema_status == "ready" or (
            schema_status is None and paper.paper_id in requested_schema_papers
        )
        if not should_probe or not callable(get_schema_instance):
            continue
        try:
            instance = get_schema_instance(paper.paper_id)
        except Exception:
            continue
        schema_ready_paper_ids.add(paper.paper_id)
        schema_field_ids.update(getattr(instance, "fields", {}).keys())

    wiki_status = getattr(gateway, "wiki_status", None)
    if callable(wiki_status):
        wiki_ready = getattr(wiki_status(), "status", None) == "ready"
    else:
        wiki_ready = bool(requested and requested.capabilities.wiki_ready)

    authoritative_tools = set(available_tools or DEFAULT_TOOLS)
    available_sources: set[str] = set()
    if schema_ready_paper_ids and "search_schema" in authoritative_tools:
        available_sources.add("schema")
    if wiki_ready and "search_wiki" in authoritative_tools:
        available_sources.add("wiki")
    if l2s1_ready_paper_ids and {
        "search_rag",
        "search_workspace_rag",
    } & authoritative_tools:
        available_sources.add("rag")

    if requested is not None:
        limits = requested.capabilities
        available_sources &= limits.available_sources
        requested_tools = set(limits.available_tools)
        if "schema" in requested_tools:
            requested_tools.add("search_schema")
        if "wiki" in requested_tools:
            requested_tools.add("search_wiki")
        if "rag" in requested_tools:
            requested_tools.update({"search_rag", "search_workspace_rag"})
        authoritative_tools &= requested_tools
        eligible_paper_ids &= limits.eligible_paper_ids
        l2s1_ready_paper_ids &= limits.l2s1_ready_paper_ids
        schema_ready_paper_ids &= limits.schema_ready_paper_ids
        schema_field_ids &= limits.schema_field_ids
        wiki_ready = wiki_ready and limits.wiki_ready
        max_actions = min(max_actions, limits.max_actions)
        max_action_limit = min(max_action_limit, limits.max_action_limit)

    return RetrievalContext(
        query=query,
        capabilities=RetrievalCapabilities(
            available_sources=available_sources,
            available_tools=authoritative_tools,
            schema_field_ids=schema_field_ids,
            eligible_paper_ids=eligible_paper_ids,
            l2s1_ready_paper_ids=l2s1_ready_paper_ids,
            schema_ready_paper_ids=schema_ready_paper_ids,
            wiki_ready=wiki_ready,
            max_actions=max_actions,
            max_action_limit=max_action_limit,
            workspace_revision=state.revision,
        ),
    )


__all__ = ["assemble_retrieval_context"]
