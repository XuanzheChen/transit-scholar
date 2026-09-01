"""Predefined run-level semantic coordination role for L3S6."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from transit_scholar.layer3.planning.models import ResearchPlan, RunDecision
from transit_scholar.layer3.run_context import RunContextSnapshot


class OptionalPlanningPolicy:
    """Small deterministic default policy, replaceable with a governed policy.

    A fresh run still needs an initial semantic route.  In production this
    lightweight policy provides a deterministic baseline until a richer policy
    is injected; importantly, it never treats an empty prior state as proof
    that the user's goal is already complete.
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

    def __init__(self, policy: Callable[[RunContextSnapshot], Any] | None = None) -> None:
        self.policy = policy or OptionalPlanningPolicy()

    def decide(self, snapshot: RunContextSnapshot | Mapping[str, Any]) -> RunDecision:
        observed = RunContextSnapshot.model_validate(snapshot)
        raw = self.policy(observed)
        return RunDecision.model_validate(raw)

    def __call__(self, snapshot: RunContextSnapshot | Mapping[str, Any]) -> RunDecision:
        return self.decide(snapshot)


__all__ = ["OptionalPlanningPolicy", "RunCoordinatorRole"]
