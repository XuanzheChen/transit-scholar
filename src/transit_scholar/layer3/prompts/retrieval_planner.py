"""Structured prompt for the semantic retrieval planner."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer3.planner import RetrievalContext


def build_retrieval_planner_prompt(context: "RetrievalContext") -> str:
    """Describe only bounded capabilities, never Workspace contents."""
    capabilities = context.capabilities
    return (
        "Plan retrieval for one already-formed research query. Select the minimal "
        "sufficient combination of schema, wiki, and rag actions; multiple ordered "
        "actions are allowed. Wiki is for navigation/discovery, schema is structured "
        "knowledge, and rag is source-grounded textual evidence. Return JSON only "
        "matching RetrievalStrategy: query_id and an ordered actions list. "
        f"Query: {context.query.query_text!r}\n"
        f"Workspace capability summary: workspace_id={context.query.workspace_id!r}; "
        f"available_sources={sorted(capabilities.available_sources)!r}; "
        f"available_tools={sorted(capabilities.available_tools)!r}; "
        f"schema_fields={sorted(capabilities.schema_field_ids)!r}; "
        f"eligible_paper_ids={sorted(capabilities.eligible_paper_ids)!r}; "
        f"wiki_ready={capabilities.wiki_ready}; "
        f"max_actions={capabilities.max_actions}; "
        f"max_action_limit={capabilities.max_action_limit}."
    )
