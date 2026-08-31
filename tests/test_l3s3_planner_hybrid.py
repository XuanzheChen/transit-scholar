"""Hybrid planner tests: LLM route selection behind deterministic guardrails."""

from __future__ import annotations

import pytest

from transit_scholar.layer3.planner import (
    HybridKnowledgeRetrievalPlanner,
    RetrievalCapabilities,
    RetrievalContext,
    StrategyValidationError,
    validate_strategy,
)
from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    ResearchQuery,
    RetrievalStrategy,
    SchemaRetrievalAction,
    WikiRetrievalAction,
)


class RecordingPlanner:
    def __init__(self, response: object) -> None:
        self.response = response
        self.prompts: list[str] = []

    def plan(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.response


def _context(**capability_overrides: object) -> RetrievalContext:
    capabilities = {
        "available_sources": {"schema", "wiki", "rag"},
        "available_tools": {"schema", "wiki", "rag"},
        "schema_field_ids": {"method", "result"},
        "eligible_paper_ids": {"paper-1", "paper-2"},
        "schema_ready_paper_ids": {"paper-1", "paper-2"},
        "l2s1_ready_paper_ids": {"paper-1", "paper-2"},
        "wiki_ready": True,
        "max_actions": 4,
        "max_action_limit": 20,
    }
    capabilities.update(capability_overrides)
    return RetrievalContext(
        query=ResearchQuery(
            query_id="query-1",
            session_id="session-1",
            workspace_id="workspace-1",
            query_text="Compare methods and supporting evidence",
        ),
        capabilities=capabilities,
    )


@pytest.mark.parametrize(
    "actions",
    [
        [RagRetrievalAction(action_id="rag", source_query="evidence")],
        [
            SchemaRetrievalAction(action_id="schema", source_query="methods"),
            RagRetrievalAction(action_id="rag", source_query="evidence"),
        ],
        [
            WikiRetrievalAction(action_id="wiki", source_query="discover"),
            RagRetrievalAction(action_id="rag", source_query="evidence"),
        ],
        [
            SchemaRetrievalAction(action_id="schema", source_query="methods"),
            WikiRetrievalAction(action_id="wiki", source_query="discover"),
            RagRetrievalAction(action_id="rag", source_query="evidence"),
        ],
        [
            WikiRetrievalAction(
                action_id="wiki", source_query="discover", discover_paper_ids=True
            ),
            RagRetrievalAction(
                action_id="rag",
                source_query="evidence",
                scope="papers",
                paper_ids=["paper-1"],
                depends_on=["wiki"],
            ),
        ],
    ],
)
def test_semantic_provider_can_select_all_required_ordered_paths(actions):
    provider = RecordingPlanner({"query_id": "query-1", "actions": actions})

    result = HybridKnowledgeRetrievalPlanner(provider).plan(_context())

    assert result.is_valid
    assert result.strategy is not None
    assert provider.prompts
    assert "keyword" not in provider.prompts[0].lower()


def test_workspace_rag_strategy_allows_partial_l2s1_readiness():
    action = RagRetrievalAction(action_id="rag", source_query="evidence")

    result = HybridKnowledgeRetrievalPlanner(
        RecordingPlanner({"query_id": "query-1", "actions": [action]})
    ).plan(_context(l2s1_ready_paper_ids={"paper-1"}))

    assert result.is_valid
    assert result.strategy is not None


def test_workspace_rag_strategy_requires_at_least_one_ready_paper():
    strategy = RetrievalStrategy(
        query_id="query-1",
        actions=[RagRetrievalAction(action_id="rag", source_query="evidence")],
    )

    with pytest.raises(StrategyValidationError, match="rag_unavailable"):
        validate_strategy(strategy, _context(l2s1_ready_paper_ids=set()))


@pytest.mark.parametrize(
    ("actions", "overrides", "error"),
    [
        (
            [SchemaRetrievalAction(action_id="schema", source_query="q")],
            {"available_sources": {"rag"}, "available_tools": {"rag"}},
            "source_unavailable",
        ),
        (
            [SchemaRetrievalAction(action_id="schema", source_query="q", field_ids=["bad"])],
            {},
            "invalid_schema_field",
        ),
        (
            [RagRetrievalAction(action_id="rag", source_query="q", scope="papers", paper_ids=["outside"])],
            {},
            "paper_outside_workspace",
        ),
        (
            [WikiRetrievalAction(action_id="wiki", source_query="q")],
            {"wiki_ready": False},
            "wiki_unready",
        ),
        (
            [RagRetrievalAction(action_id="rag", source_query="q")],
            {"available_tools": {"schema", "wiki"}},
            "tool_unavailable",
        ),
        (
            [RagRetrievalAction(action_id="rag", source_query="q", limit=21)],
            {},
            "action_limit_exceeded",
        ),
    ],
)
def test_deterministic_validation_blocks_unavailable_or_unsafe_actions(actions, overrides, error):
    strategy = RetrievalStrategy(query_id="query-1", actions=actions)

    with pytest.raises(StrategyValidationError, match=error):
        validate_strategy(strategy, _context(**overrides))


@pytest.mark.parametrize(
    "response",
    ["not JSON", {"query_id": "query-1", "actions": [{"source_kind": "unknown"}]}],
)
def test_malformed_llm_output_returns_explicit_safe_failure(response):
    result = HybridKnowledgeRetrievalPlanner(RecordingPlanner(response)).plan(_context())

    assert result.strategy is None
    assert result.diagnostics[0].code == "planner_validation_failed"
    assert result.diagnostics[0].status == "failed"
