"""T-010 regression coverage for the minimum Layer2/Layer3 provider repairs.

Each test here pins one defect that blocked the configured OpenAI-compatible
provider chain (OpenCode Go / ``deepseek-v4-flash``). They are deterministic and
offline: no network, no provider, and no deterministic substitution of the real
runtime composition — the real provider behaviour is exercised by
``tests/ui/test_product_e2e_smoke.py``.
"""

from __future__ import annotations

import pytest

from transit_scholar.layer3.planner import RetrievalCapabilities, RetrievalContext
from transit_scholar.layer3.planning import ResearchPlanItem, RunDecision
from transit_scholar.layer3.prompts.builtin_roles import (
    CLAIM_REASONING_PROMPT,
    EVIDENCE_REASONING_PROMPT,
    FINAL_SYNTHESIS_PROMPT,
    QUERY_PLANNING_PROMPT,
    RESEARCH_COORDINATOR_PROMPT,
)
from transit_scholar.layer3.prompts.retrieval_planner import build_retrieval_planner_prompt
from transit_scholar.layer3.prompts.run_coordination import build_run_coordination_prompt
from transit_scholar.layer3.retrieval import ResearchQuery
from transit_scholar.layer3.run_context import (
    RunContextSnapshot,
    RunOrchestrationState,
    RunRuntimeConfig,
)
from transit_scholar.layer3.runtime.run_runtime import RunResearchRuntime
from transit_scholar.layer3.synthesis import RunFinalSynthesisRole

# ---------------------------------------------------------------------------
# prompt submission contract (the real model returned completed=false)
# ---------------------------------------------------------------------------

ROLE_PROMPTS = {
    "research_coordinator": RESEARCH_COORDINATOR_PROMPT,
    "query_planning": QUERY_PLANNING_PROMPT,
    "evidence_reasoning": EVIDENCE_REASONING_PROMPT,
    "claim_reasoning": CLAIM_REASONING_PROMPT,
    "final_synthesis": FINAL_SYNTHESIS_PROMPT,
}


def test_every_role_prompt_states_the_structured_submission_contract():
    """A Role step is submitted by ``completed=true``; the prompt must say so."""
    for role_id, prompt in ROLE_PROMPTS.items():
        assert "completed=true" in prompt, role_id
        assert '"completed": true' in prompt, role_id
        assert "JSON" in prompt, role_id


def test_coordinator_prompt_documents_advancement_instead_of_iteration():
    """The session coordinator must advance stages, not re-run finished ones."""
    prompt = RESEARCH_COORDINATOR_PROMPT
    assert '"completed": true, "next_role_id"' in prompt
    for stage in (
        "query_planning",
        "evidence_reasoning",
        "claim_reasoning",
        "final_synthesis",
    ):
        assert stage in prompt
    assert "one bounded execution" in prompt
    assert "final_synthesis" in prompt.split("Once accepted evidence")[1]


def test_role_prompts_require_verbatim_evidence_identifiers():
    """Unknown IDs are dropped or rejected downstream, so prompts must warn."""
    assert "verbatim" in EVIDENCE_REASONING_PROMPT
    assert "never invent an ID" in EVIDENCE_REASONING_PROMPT
    assert "verbatim" in CLAIM_REASONING_PROMPT
    assert "never invent an ID" in CLAIM_REASONING_PROMPT
    assert "verbatim" in FINAL_SYNTHESIS_PROMPT


# ---------------------------------------------------------------------------
# retrieval planner prompt guidance (strategy validation rejected every plan)
# ---------------------------------------------------------------------------


def _context(query_id: str = "q-t010") -> RetrievalContext:
    return RetrievalContext(
        query=ResearchQuery(
            query_id=query_id,
            session_id="session-t010",
            workspace_id="workspace-t010",
            query_text="train scheduling on single-track railway networks",
        ),
        capabilities=RetrievalCapabilities(
            available_sources={"rag"},
            eligible_paper_ids={"paper-1"},
            wiki_ready=False,
        ),
    )


def test_retrieval_planner_prompt_states_the_exact_query_id():
    """``validate_strategy`` requires the strategy to echo the caller's id."""
    prompt = build_retrieval_planner_prompt(_context("q-t010"))
    assert "query_id must be exactly 'q-t010'" in prompt
    assert "train scheduling on single-track railway networks" in prompt


def test_retrieval_planner_prompt_states_the_required_action_shape():
    prompt = build_retrieval_planner_prompt(_context())
    for field in ("action_id", "source_kind", "source_query", "limit"):
        assert field in prompt, field
    assert "scope" in prompt
    assert "paper_ids" in prompt
    assert "available_sources" in prompt


# ---------------------------------------------------------------------------
# plan-update sequencing (first-round planned_research raised ValueError)
# ---------------------------------------------------------------------------


def _runtime(decisions: list[RunDecision]) -> RunResearchRuntime:
    pending = list(decisions)

    def coordinator(snapshot):
        return pending.pop(0) if pending else RunDecision(mode="complete", completion_reason="done")

    return RunResearchRuntime(
        session_runtime=lambda session, handoff: {
            "status": "completed",
            "final_response": "session answer",
        },
        coordinator=coordinator,
        synthesis=RunFinalSynthesisRole(),
        config=RunRuntimeConfig(),
    )


def test_invalid_first_round_plan_updates_are_reasked_then_corrected():
    """A sequencing slip is re-asked; a corrected decision still runs the plan.

    The runtime's acceptance rule is unchanged (an update with no plan, or for
    an unknown item, is still rejected). The provider's first answer is simply
    re-asked a bounded number of times, because the configured real provider
    sometimes expresses its first plan through ``plan_item_updates``.
    """
    result = _runtime(
        [
            RunDecision(
                mode="planned_research",
                plan_item_updates=[
                    ResearchPlanItem(
                        item_id="item-1",
                        research_question="What methods are reported?",
                        order=0,
                    )
                ],
            ),
            RunDecision(
                mode="planned_research",
                proposed_questions=["What methods are reported?"],
            ),
            RunDecision(mode="complete", completion_reason="sufficient"),
        ]
    ).execute(agent_run_id="run-t010-first-round", user_goal="goal")

    assert result["status"] == "completed"
    plan = result["research_plan"]
    assert plan is not None, "the corrected first-round decision produced no plan"
    assert [item.research_question for item in plan.items] == [
        "What methods are reported?"
    ]
    assert plan.items[0].order == 0
    assert plan.items[0].research_session_id, "the plan item never ran a session"
    assert result["session_outcomes"], "no research session ran"


def test_insisting_on_invalid_plan_updates_still_raises_value_error():
    """The frozen sequencing rejection survives the bounded re-ask."""
    slip = RunDecision(
        mode="planned_research",
        plan_item_updates=[
            ResearchPlanItem(item_id="unknown", research_question="q", order=0)
        ],
    )
    runtime = RunResearchRuntime(
        session_runtime=lambda session, handoff: {
            "status": "completed",
            "final_response": "session answer",
        },
        coordinator=lambda snapshot: slip,
        synthesis=RunFinalSynthesisRole(),
        config=RunRuntimeConfig(),
    )
    with pytest.raises(ValueError):
        runtime.execute(agent_run_id="run-t010-insisting", user_goal="goal")


def test_existing_pending_plan_reasks_direct_session_then_runs_pending_item():
    """A semantic decision cannot bypass and strand an authoritative plan."""
    result = _runtime(
        [
            RunDecision(
                mode="planned_research",
                proposed_questions=[
                    "What methods are reported?",
                    "What results are reported?",
                ],
            ),
            RunDecision(
                mode="direct_session",
                proposed_questions=["Repeat the overall goal"],
            ),
            RunDecision(mode="planned_research"),
            RunDecision(mode="complete", completion_reason="sufficient"),
        ]
    ).execute(agent_run_id="run-t010-no-plan-bypass", user_goal="goal")

    assert result["status"] == "completed"
    assert [outcome.research_question for outcome in result["session_outcomes"]] == [
        "What methods are reported?",
        "What results are reported?",
    ]


def test_non_planning_mode_cannot_hide_plan_mutations():
    plan_item = ResearchPlanItem(
        item_id="item-1", research_question="What methods are reported?", order=0
    )
    decision = RunDecision(
        mode="direct_session",
        proposed_questions=["question"],
        plan_item_updates=[plan_item],
    )
    assert RunResearchRuntime._plan_sequencing_error(decision, None) == (
        "direct_session cannot carry plan mutations"
    )


def test_run_coordination_prompt_pins_top_level_shape_and_plan_progression():
    prompt = build_run_coordination_prompt(
        RunContextSnapshot(
            agent_run_id="run-t010-prompt",
            user_goal="goal",
            orchestration_state=RunOrchestrationState(
                agent_run_id="run-t010-prompt"
            ),
        )
    )
    assert "mode=planned_research" in prompt
    assert "exactly one focused question in proposed_questions" in prompt
    assert "metadata is explanatory only" in prompt
    assert '"plan_item_updates": []' in prompt


# ---------------------------------------------------------------------------
# run-coordination provider tolerance (a single 502/stall killed the run)
# ---------------------------------------------------------------------------


def test_run_coordination_retries_transient_provider_failures(monkeypatch):
    """The run-level decision must tolerate transient provider noise."""
    from transit_scholar.layer2.schema_extraction.errors import LLMRequestError
    from transit_scholar.layer3.roles.run_coordinator import (
        _PROVIDER_RETRY_LIMIT,
        SemanticRunCoordinationPolicy,
    )

    calls = {"n": 0}
    decision = RunDecision(mode="complete", completion_reason="sufficient")

    class FlakyDecider:
        def decide(self, context):
            calls["n"] += 1
            if calls["n"] <= _PROVIDER_RETRY_LIMIT:
                raise LLMRequestError("LLM provider returned HTTP 502", status_code=502)
            return decision

    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    decider = FlakyDecider()
    policy = SemanticRunCoordinationPolicy(semantic_decider=decider)

    assert policy._decide_with_provider_retry(decider, object()) == decision
    assert calls["n"] == _PROVIDER_RETRY_LIMIT + 1


def test_run_coordination_gives_up_after_the_provider_retry_budget(monkeypatch):
    from transit_scholar.layer2.schema_extraction.errors import LLMRequestError
    from transit_scholar.layer3.roles.run_coordinator import (
        _PROVIDER_RETRY_LIMIT,
        SemanticRunCoordinationPolicy,
    )

    calls = {"n": 0}

    class AlwaysFailing:
        def decide(self, context):
            calls["n"] += 1
            raise LLMRequestError("LLM provider returned HTTP 502", status_code=502)

    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    policy = SemanticRunCoordinationPolicy(semantic_decider=AlwaysFailing())

    with pytest.raises(LLMRequestError):
        policy._decide_with_provider_retry(AlwaysFailing(), object())
    assert calls["n"] == _PROVIDER_RETRY_LIMIT + 1


def test_run_coordination_surfaces_capability_rejections_immediately(monkeypatch):
    """A structured-output capability rejection is a fact, never retried."""
    from transit_scholar.layer2.schema_extraction.errors import LLMCapabilityError
    from transit_scholar.layer3.roles.run_coordinator import (
        SemanticRunCoordinationPolicy,
    )

    calls = {"n": 0}

    class Rejecting:
        def decide(self, context):
            calls["n"] += 1
            raise LLMCapabilityError("json_schema unavailable", status_code=400)

    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    policy = SemanticRunCoordinationPolicy(semantic_decider=Rejecting())

    with pytest.raises(LLMCapabilityError):
        policy._decide_with_provider_retry(Rejecting(), object())
    assert calls["n"] == 1
