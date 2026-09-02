"""Predefined run-level semantic coordination role for L3S6."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from transit_scholar.layer3.planning import (
    ResearchPlan,
    RunDecision,
    StructuredRunSemanticDecider,
)
from transit_scholar.layer3.run_context import RunContextSnapshot


class SemanticRunCoordinationPolicy:
    """Production coordination adapter for governed semantic decision makers.

    ``semantic_decider`` is intentionally injectable (an LLM/provider adapter,
    or a deterministic test double).  The small local behavior is only a safe
    degraded mode: it uses run state rather than lexical goal heuristics.
    """

    def __init__(self, semantic_decider: Callable[[RunContextSnapshot], Any] | None = None) -> None:
        self.semantic_decider = semantic_decider

    def __call__(self, snapshot: RunContextSnapshot) -> RunDecision:
        if self.semantic_decider is not None:
            decider = self.semantic_decider
            if hasattr(decider, "decide"):
                raw = decider.decide(snapshot)
            else:
                raw = decider(snapshot)
            return RunDecision.model_validate(raw)
        if snapshot.research_plan is not None:
            plan = ResearchPlan.model_validate(snapshot.research_plan)
            if any(item.status == "pending" for item in plan.items):
                return RunDecision(mode="planned_research")
        if snapshot.unresolved_items or snapshot.conflicting_items:
            return RunDecision(
                mode="planned_research",
                proposed_questions=[*snapshot.unresolved_items, *snapshot.conflicting_items],
            )
        if not snapshot.session_outcomes:
            return RunDecision(mode="direct_session", proposed_questions=[snapshot.user_goal])
        return RunDecision(mode="complete", completion_reason="research_sufficient")


SemanticRunCoordinatorPolicy = SemanticRunCoordinationPolicy


class OptionalPlanningPolicy:
    """Small deterministic fallback policy for tests or degraded mode.

    A fresh run still needs an initial route. This policy remains available for
    explicit injection, but the production composition uses a semantic
    decision-maker instead; importantly, it never treats an empty prior state
    as proof that the user's goal is already complete.
    """

    _planning_markers = (
        "compare", "comprehensive", "multiple", "several", "plan",
        "investigate", "research", "analyze", "analysis", "review",
    )

    def __call__(self, snapshot: RunContextSnapshot) -> RunDecision:
        if snapshot.research_plan is not None:
            plan = ResearchPlan.model_validate(snapshot.research_plan)
            pending = [item for item in plan.items if item.status == "pending"]
            if pending:
                return RunDecision(mode="planned_research")
        if snapshot.unresolved_items or snapshot.conflicting_items:
            return RunDecision(
                mode="planned_research",
                proposed_questions=[*snapshot.unresolved_items, *snapshot.conflicting_items],
            )

        if not snapshot.session_outcomes and snapshot.research_plan is None:
            goal = snapshot.user_goal.strip()
            normalized = goal.casefold()
            requires_plan = (
                len(goal.split()) >= 20
                or any(marker in normalized for marker in self._planning_markers)
                or any(token in normalized for token in (" and ", " vs ", " versus "))
            )
            if requires_plan:
                return RunDecision(mode="planned_research", proposed_questions=[goal])
            return RunDecision(mode="direct_session", proposed_questions=[goal])

        return RunDecision(mode="complete", completion_reason="research_sufficient")


class RunCoordinatorRole:
    """Fixed, schema-validating run coordinator; never creates roles or agents."""

    role_id = "run_coordinator"
    prompt_template = "Choose direct_session, planned_research, or complete from RunContextSnapshot."

    def __init__(
        self,
        policy: Callable[[RunContextSnapshot], Any] | None = None,
        *,
        semantic_decider: Callable[[RunContextSnapshot], Any] | None = None,
    ) -> None:
        if policy is not None and semantic_decider is not None:
            raise ValueError("provide policy or semantic_decider, not both")
        if semantic_decider is not None:
            policy = SemanticRunCoordinationPolicy(semantic_decider=semantic_decider)
        self.policy = policy if policy is not None else SemanticRunCoordinationPolicy()

    def decide(self, snapshot: RunContextSnapshot | Mapping[str, Any]) -> RunDecision:
        observed = RunContextSnapshot.model_validate(snapshot)
        raw = self.policy(observed)
        return RunDecision.model_validate(raw)

    def __call__(self, snapshot: RunContextSnapshot | Mapping[str, Any]) -> RunDecision:
        return self.decide(snapshot)


def build_run_coordinator(
    *,
    semantic_decider: Callable[[RunContextSnapshot], Any] | None = None,
    llm_client: Any | None = None,
    llm_config: Any | None = None,
    policy: Callable[[RunContextSnapshot], Any] | None = None,
) -> RunCoordinatorRole:
    """Build the production coordinator with an explicit semantic boundary.

    Supplying ``policy`` is an explicit override for deterministic tests or a
    deliberately selected degraded mode. Without it, the role always receives
    a concrete structured semantic decider; no implicit deterministic policy is
    selected by this production composition.
    """
    if policy is not None and any(value is not None for value in (semantic_decider, llm_client, llm_config)):
        raise ValueError("policy cannot be combined with semantic coordinator dependencies")
    if policy is not None:
        return RunCoordinatorRole(policy=policy)
    decider = (
        semantic_decider
        if semantic_decider is not None
        else StructuredRunSemanticDecider(llm_client, llm_config=llm_config)
    )
    return RunCoordinatorRole(semantic_decider=decider)


build_production_run_coordinator = build_run_coordinator
create_production_run_coordinator = build_run_coordinator


def build_fallback_run_coordinator(
    policy: Callable[[RunContextSnapshot], Any] | None = None,
) -> RunCoordinatorRole:
    """Build a coordinator using an explicitly selected deterministic policy."""
    return RunCoordinatorRole(
        policy=policy if policy is not None else OptionalPlanningPolicy()
    )


class RunCoordinatorFactory:
    """Reusable production composition object with injectable LLM boundary."""

    def __init__(
        self,
        *,
        semantic_decider: Callable[[RunContextSnapshot], Any] | None = None,
        llm_client: Any | None = None,
        llm_config: Any | None = None,
    ) -> None:
        self.semantic_decider = semantic_decider
        self.llm_client = llm_client
        self.llm_config = llm_config

    def build(self) -> RunCoordinatorRole:
        return build_run_coordinator(
            semantic_decider=self.semantic_decider,
            llm_client=self.llm_client,
            llm_config=self.llm_config,
        )

    __call__ = build


__all__ = [
    "OptionalPlanningPolicy",
    "RunCoordinatorFactory",
    "RunCoordinatorRole",
    "SemanticRunCoordinationPolicy",
    "SemanticRunCoordinatorPolicy",
    "StructuredRunSemanticDecider",
    "build_fallback_run_coordinator",
    "build_production_run_coordinator",
    "build_run_coordinator",
    "create_production_run_coordinator",
]
