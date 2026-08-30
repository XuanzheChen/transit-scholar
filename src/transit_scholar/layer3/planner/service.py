"""Hybrid planner: constraints, semantic LLM plan, then validation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from transit_scholar.layer3.prompts import build_retrieval_planner_prompt
from transit_scholar.layer3.retrieval import RetrievalStrategy

from .models import PlanningResult, RetrievalContext
from .validation import StrategyValidationError, failure_diagnostic, validate_strategy


class RetrievalPlannerProvider(Protocol):
    """Injectable semantic planner provider with no vendor runtime coupling."""

    def plan(self, prompt: str) -> str | dict[str, Any] | RetrievalStrategy: ...


class HybridKnowledgeRetrievalPlanner:
    """Use an LLM for route selection while retaining deterministic safety gates."""

    def __init__(self, provider: RetrievalPlannerProvider | Callable[[str], object]) -> None:
        self.provider = provider

    def plan(self, context: RetrievalContext) -> PlanningResult:
        """Return an executable strategy only if all deterministic checks pass."""
        try:
            proposed = self._request_plan(build_retrieval_planner_prompt(context))
            strategy = self._parse_strategy(proposed)
            validate_strategy(strategy, context)
        except (StrategyValidationError, ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
            return PlanningResult(diagnostics=[failure_diagnostic(error)])
        return PlanningResult(strategy=strategy)

    def _request_plan(self, prompt: str) -> object:
        plan_method = getattr(self.provider, "plan", None)
        return plan_method(prompt) if callable(plan_method) else self.provider(prompt)

    @staticmethod
    def _parse_strategy(value: object) -> RetrievalStrategy:
        if isinstance(value, RetrievalStrategy):
            return value
        if isinstance(value, str):
            return RetrievalStrategy.model_validate(json.loads(value))
        if isinstance(value, dict):
            return RetrievalStrategy.model_validate(value)
        raise TypeError("planner output must be a RetrievalStrategy, JSON object, or JSON string")
