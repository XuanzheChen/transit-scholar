import json

import pytest
from pydantic import ValidationError

from transit_scholar.layer3.memory import (
    EpisodicMemoryDistiller,
    NormalizedEpisodeInput,
    build_episodic_record,
)


def _normalized() -> NormalizedEpisodeInput:
    return NormalizedEpisodeInput(
        workspace_id="workspace-1",
        agent_run_id="run-1",
        user_goal_raw="Exact raw goal",
        session_ids=("session-1",),
        useful_queries=("useful",),
        failed_or_unhelpful_queries=("failed",),
        final_outcome="completed",
        claim_ids=("claim-1",),
    )


class RecordingClient:
    def __init__(self, response):
        self.response = response
        self.messages = None
        self.schema = None
        self.metadata = None

    def generate_structured(self, messages, output_schema, metadata=None):
        self.messages = messages
        self.schema = output_schema
        self.metadata = metadata
        return self.response


def test_distiller_receives_only_bounded_normalized_input_and_schema() -> None:
    client = RecordingClient({
        "goal_summary": "Semantic goal",
        "research_summary": "Semantic research summary",
        "important_claim_ids": ["claim-1"],
        "unresolved_summary": "Remaining question",
    })

    semantic = EpisodicMemoryDistiller(client).distill(_normalized())

    payload = json.loads(client.messages[0]["content"])
    assert payload == _normalized().model_dump(mode="json")
    assert client.metadata["normalized_episode_input"] == payload
    assert client.schema.__name__ == "EpisodicSemanticOutput"
    assert semantic.goal_summary == "Semantic goal"


def test_distiller_rejects_free_form_or_extra_control_output() -> None:
    client = RecordingClient({
        "goal_summary": "goal",
        "research_summary": "summary",
        "important_claim_ids": [],
        "unresolved_summary": "",
        "replace_user_goal_raw": "invented",
    })

    with pytest.raises(ValidationError):
        EpisodicMemoryDistiller(client).distill(_normalized())


def test_semantic_output_cannot_replace_raw_goal_or_deterministic_fields() -> None:
    client = RecordingClient({
        "goal_summary": "Rewritten semantic goal",
        "research_summary": "summary",
        "important_claim_ids": ["claim-1"],
        "unresolved_summary": "",
    })
    semantic = EpisodicMemoryDistiller(client).distill(_normalized())

    record = build_episodic_record(_normalized(), semantic)

    assert record.user_goal_raw == "Exact raw goal"
    assert record.goal_summary == "Rewritten semantic goal"
    assert record.useful_queries == ("useful",)
    assert record.failed_or_unhelpful_queries == ("failed",)
    assert record.final_outcome == "completed"
