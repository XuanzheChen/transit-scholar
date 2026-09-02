"""Bounded projection used by the semantic L3S6 RunCoordinator."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .models import (
    RunContextSnapshot,
    RunCoordinatorContext,
    RunCoordinatorSessionSummary,
    RunRuntimeConfig,
    SessionOutcome,
)


_EXCLUDED_KEYS = frozenset(
    {
        "source_provenance",
        "text_snapshot",
        "evidence",
        "retrieval_history",
        "agent_trace",
        "provider_history",
        "role_execution_history",
        "prompt_history",
    }
)


class RunCoordinatorContextProjector:
    """Project a complete snapshot into deterministic run-level information.

    The projector deliberately constructs new value objects instead of
    filtering a snapshot dump. This makes low-level Session execution state
    impossible to forward accidentally and gives the semantic boundary one
    place to enforce item and serialized-size limits.
    """

    def __init__(self, config: RunRuntimeConfig | None = None) -> None:
        self.config = config or RunRuntimeConfig()

    def project(
        self,
        snapshot: RunContextSnapshot,
        *,
        config: RunRuntimeConfig | None = None,
    ) -> RunCoordinatorContext:
        observed = RunContextSnapshot.model_validate(snapshot)
        cfg = config or self.config
        required = RunCoordinatorContext(
            agent_run_id=observed.agent_run_id,
            user_goal=observed.user_goal,
        )
        if self._serialized_size(required) > cfg.max_serialized_chars:
            raise ValueError(
                "max_serialized_chars is too small for required coordination content"
            )

        outcomes = list(observed.session_outcomes)
        if cfg.max_prior_sessions:
            outcomes = outcomes[-cfg.max_prior_sessions :]
        else:
            outcomes = []
        context = RunCoordinatorContext(
            agent_run_id=observed.agent_run_id,
            user_goal=observed.user_goal,
            research_plan=self._plan_view(observed.research_plan, cfg),
            prior_sessions=[self._session_view(outcome, cfg) for outcome in outcomes],
            key_claims=self._key_claims(outcomes, cfg),
            claim_refs=self._bounded_refs(
                [*observed.claim_refs, *(ref for outcome in outcomes for ref in outcome.claim_refs)],
                self._coordination_limit(cfg, "max_coordination_claim_refs"),
            ),
            unresolved_items=self._bounded_strings(
                observed.unresolved_items,
                self._coordination_limit(cfg, "max_coordination_unresolved_items"),
            ),
            conflicting_items=self._bounded_strings(
                observed.conflicting_items,
                self._coordination_limit(cfg, "max_coordination_conflicting_items"),
            ),
            active_session_ids=self._bounded_refs(
                observed.active_session_ids, cfg.max_prior_sessions
            ),
            failed_session_ids=self._bounded_refs(
                observed.failed_session_ids, cfg.max_prior_sessions
            ),
            orchestration_state=self._orchestration_view(observed),
        )
        return self._fit_budget(context, cfg.max_serialized_chars)

    @staticmethod
    def _serialized_size(context: RunCoordinatorContext) -> int:
        return len(context.model_dump_json())

    def _fit_budget(
        self,
        context: RunCoordinatorContext,
        max_chars: int,
    ) -> RunCoordinatorContext:
        if self._serialized_size(context) <= max_chars:
            return context

        context.orchestration_state = None
        context.research_plan = None
        while self._serialized_size(context) > max_chars:
            if context.key_claims:
                context.key_claims.pop()
            elif context.claim_refs:
                context.claim_refs.pop()
            elif context.unresolved_items:
                context.unresolved_items.pop()
            elif context.conflicting_items:
                context.conflicting_items.pop()
            elif context.prior_sessions:
                context.prior_sessions.pop()
            elif context.active_session_ids:
                context.active_session_ids.pop()
            elif context.failed_session_ids:
                context.failed_session_ids.pop()
            else:
                break
        if self._serialized_size(context) > max_chars:
            raise ValueError("max_serialized_chars is too small for coordination content")
        return context

    def _plan_view(self, plan: Any | None, config: RunRuntimeConfig) -> dict[str, Any] | None:
        if plan is None:
            return None
        raw = plan.model_dump(mode="python") if hasattr(plan, "model_dump") else plan
        if not isinstance(raw, Mapping):
            return None
        items = raw.get("items", ())
        bounded_items: list[dict[str, Any]] = []
        if isinstance(items, list):
            ordered = sorted(
                (item for item in items if isinstance(item, Mapping)),
                key=lambda item: (self._as_int(item.get("order")), str(item.get("item_id", ""))),
            )
            for item in ordered[: self._coordination_limit(config, "max_coordination_plan_items")]:
                item_id = self._as_text(item.get("item_id"))
                question = self._as_text(item.get("research_question"))
                if not item_id or not question:
                    continue
                bounded_items.append(
                    {
                        "item_id": item_id,
                        "research_question": question[:2000],
                        "order": self._as_int(item.get("order")),
                        "status": self._as_text(item.get("status")) or "pending",
                        "research_session_id": self._optional_text(item.get("research_session_id")),
                    }
                )
        return {
            "plan_id": self._optional_text(raw.get("plan_id")),
            "planning_round": self._as_int(raw.get("planning_round")),
            "items": bounded_items,
        }

    def _session_view(
        self, outcome: SessionOutcome, config: RunRuntimeConfig
    ) -> RunCoordinatorSessionSummary:
        return RunCoordinatorSessionSummary(
            research_session_id=outcome.research_session_id,
            research_question=outcome.research_question[:2000],
            status=outcome.status,
            final_summary=self._optional_text(outcome.final_summary or outcome.final_response, 4000),
            failure_reason=self._optional_text(outcome.failure_reason, 2000),
            claim_refs=self._bounded_refs(
                outcome.claim_refs,
                self._coordination_limit(config, "max_coordination_claim_refs"),
            ),
            evidence_refs=self._bounded_refs(
                outcome.evidence_refs,
                self._coordination_limit(config, "max_coordination_claim_refs"),
            ),
            source_refs=self._bounded_refs(
                outcome.source_refs,
                self._coordination_limit(config, "max_coordination_claim_refs"),
            ),
        )

    def _key_claims(
        self, outcomes: list[SessionOutcome], config: RunRuntimeConfig
    ) -> list[str]:
        values: list[str] = []
        limit = self._coordination_limit(config, "max_coordination_claims")
        if limit <= 0:
            return values
        for outcome in outcomes:
            for claim in outcome.key_claims[: config.max_claims_per_session]:
                compact = self._compact_claim(claim)
                if compact:
                    values.append(compact)
                if len(values) >= limit:
                    return values
        return values

    def _compact_claim(self, claim: Any) -> str:
        if hasattr(claim, "model_dump"):
            claim = claim.model_dump(mode="python")
        if isinstance(claim, str):
            return claim[:2000]
        if isinstance(claim, Mapping):
            cleaned = self._strip_excluded(claim)
            for key in ("claim", "statement", "summary", "claim_text", "claim_id", "id"):
                value = cleaned.get(key)
                if isinstance(value, str) and value:
                    return value[:2000]
            try:
                return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)[:2000]
            except (TypeError, ValueError):
                return ""
        if isinstance(claim, (list, tuple, set, frozenset)):
            values = list(claim)
            if isinstance(claim, (set, frozenset)):
                values.sort(key=lambda item: repr(item))
            cleaned = [self._strip_excluded_value(item) for item in values[:20]]
            try:
                return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)[:2000]
            except (TypeError, ValueError):
                return ""
        return self._as_text(claim)[:2000]

    @staticmethod
    def _coordination_limit(config: RunRuntimeConfig, field: str) -> int:
        return min(getattr(config, field), config.max_handoff_items)

    def _orchestration_view(self, snapshot: RunContextSnapshot) -> dict[str, Any] | None:
        state = snapshot.orchestration_state
        if state is None:
            return None
        return {
            "status": state.status,
            "current_plan_item_id": state.current_plan_item_id,
            "current_research_session_id": state.current_research_session_id,
            "planning_round": state.planning_round,
            "run_steps": state.run_steps,
            "completed_session_ids": list(state.completed_session_ids),
            "failed_session_ids": list(state.failed_session_ids),
            "termination_reason": state.termination_reason,
        }

    @staticmethod
    def _strip_excluded(value: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).casefold() in _EXCLUDED_KEYS:
                continue
            result[str(key)] = RunCoordinatorContextProjector._strip_excluded_value(item)
        return result

    @staticmethod
    def _strip_excluded_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return RunCoordinatorContextProjector._strip_excluded(value)
        if isinstance(value, (list, tuple, set, frozenset)):
            values = list(value)
            if isinstance(value, (set, frozenset)):
                values.sort(key=lambda item: repr(item))
            return [
                RunCoordinatorContextProjector._strip_excluded_value(item)
                for item in values[:20]
            ]
        return value

    @staticmethod
    def _bounded_refs(values: Any, limit: int) -> list[str]:
        result: list[str] = []
        if limit <= 0:
            return result
        for value in values or ():
            text = str(value)
            if text and text not in result:
                result.append(text[:500])
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _bounded_strings(values: Any, limit: int) -> list[str]:
        return [str(value)[:2000] for value in (values or ())][: max(0, limit)]

    @staticmethod
    def _as_text(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _optional_text(value: Any, limit: int = 2000) -> str | None:
        text = RunCoordinatorContextProjector._as_text(value)
        return text[:limit] if text else None

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


SemanticCoordinationContext = RunCoordinatorContext
SemanticCoordinationContextProjector = RunCoordinatorContextProjector
RunCoordinationContext = RunCoordinatorContext
RunCoordinationContextProjector = RunCoordinatorContextProjector
RunCoordinatorSemanticContext = RunCoordinatorContext
RunCoordinatorSemanticContextProjector = RunCoordinatorContextProjector


__all__ = [
    "RunCoordinatorContextProjector",
    "RunCoordinatorContext",
    "RunCoordinatorSessionSummary",
    "SemanticCoordinationContext",
    "SemanticCoordinationContextProjector",
    "RunCoordinationContext",
    "RunCoordinationContextProjector",
    "RunCoordinatorSemanticContext",
    "RunCoordinatorSemanticContextProjector",
]
