"""Layer3 Stage3 query and retrieval contract tests."""

from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    ResearchQuery,
    RetrievalStrategy,
    SchemaRetrievalAction,
    WikiRetrievalAction,
)
from transit_scholar.layer3.tools import RetrievalResultEnvelope


def test_queries_from_one_session_are_independently_executable():
    first = ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="What is the headway?",
    )
    second = ResearchQuery(
        query_id="query-2",
        session_id="session-1",
        workspace_id="workspace-1",
        text="What evidence supports it?",
    )

    assert first.session_id == second.session_id
    assert first.query_id != second.query_id
    assert first.text == first.query_text


@pytest.mark.parametrize(
    ("actions", "source_kinds"),
    [
        ([RagRetrievalAction(action_id="rag", source_query="q")], ["rag"]),
        (
            [
                SchemaRetrievalAction(action_id="schema", source_query="q"),
                RagRetrievalAction(action_id="rag", source_query="q"),
            ],
            ["schema", "rag"],
        ),
        (
            [
                WikiRetrievalAction(action_id="wiki", source_query="q"),
                RagRetrievalAction(action_id="rag", source_query="q"),
            ],
            ["wiki", "rag"],
        ),
        (
            [
                SchemaRetrievalAction(action_id="schema", source_query="q"),
                WikiRetrievalAction(action_id="wiki", source_query="q"),
                RagRetrievalAction(action_id="rag", source_query="q"),
            ],
            ["schema", "wiki", "rag"],
        ),
        (
            [
                WikiRetrievalAction(
                    action_id="discover", source_query="q", discover_paper_ids=True
                ),
                RagRetrievalAction(
                    action_id="rag",
                    source_query="q",
                    scope="papers",
                    paper_ids=["paper-1"],
                    depends_on=["discover"],
                ),
            ],
            ["wiki", "rag"],
        ),
    ],
)
def test_strategy_supports_required_multi_source_paths(actions, source_kinds):
    strategy = RetrievalStrategy(query_id="query-1", actions=actions)

    restored = RetrievalStrategy.model_validate_json(strategy.model_dump_json())

    assert [action.source_kind for action in restored.actions] == source_kinds


def test_strategy_rejects_future_or_unknown_dependencies():
    with pytest.raises(ValidationError, match="earlier actions"):
        RetrievalStrategy(
            query_id="query-1",
            actions=[
                RagRetrievalAction(
                    action_id="rag", source_query="q", depends_on=["wiki"]
                ),
                WikiRetrievalAction(action_id="wiki", source_query="q"),
            ],
        )


def test_unified_envelope_preserves_separate_result_semantics():
    query = ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="Find sources",
    )
    result = RetrievalResultEnvelope(
        query=query,
        schema_results=[
            {"action_id": "schema", "field_id": "method", "value": "A"}
        ],
        wiki_results=[
            {"action_id": "wiki", "node_id": "node-1", "title": "Paper"}
        ],
    )

    assert result.schema_results[0].field_id == "method"
    assert result.wiki_results[0].node_id == "node-1"


def test_contracts_import_without_agent_runtime_dependencies():
    assert importlib.import_module("transit_scholar.layer3.retrieval").ResearchQuery is ResearchQuery
    assert importlib.import_module("transit_scholar.layer3.tools").RetrievalResultEnvelope is RetrievalResultEnvelope
