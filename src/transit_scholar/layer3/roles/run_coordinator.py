"""Predefined run-level semantic coordination role for L3S6."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from transit_scholar.layer3.planning.models import ResearchPlan, RunDecision
from transit_scholar.layer3.run_context import RunContextSnapshot


class OptionalPlanningPolicy:
    """Small deterministic default policy, replaceable with a governed policy."""

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
