"""Deterministic least-context projection for predefined Roles."""

from __future__ import annotations

import json
from typing import Any

from transit_scholar.layer3.agent import ContextPolicy, RoleDefinition

from .models import CONTEXT_SECTIONS, RoleContext, RuntimeContextSnapshot


class InvalidContextPolicyError(ValueError):
    pass


class ContextBudgetExceededError(ValueError):
    pass


class RoleContextProjector:
    """Project only policy-allowed sections; no snapshot reference is retained."""

    def project(
        self,
        snapshot: RuntimeContextSnapshot,
        role: RoleDefinition,
    ) -> RoleContext:
        policy = role.context_policy
        unknown = policy.included_sections - CONTEXT_SECTIONS
        if unknown:
            raise InvalidContextPolicyError(
                f"unknown context sections: {', '.join(sorted(unknown))}"
            )
        snapshot_data = snapshot.model_dump(mode="json")
        sections: dict[str, Any] = {}
        truncated = False
        for name in sorted(policy.included_sections):
            value = snapshot_data[name]
            if policy.max_items_per_section is not None and isinstance(value, list):
                limited = value[: policy.max_items_per_section]
                truncated = truncated or len(limited) != len(value)
                value = limited
            candidate = {**sections, name: value}
            if self._serialized_chars(candidate) > (policy.max_serialized_chars or 10**18):
                truncated = True
                continue
            sections = candidate
        serialized_chars = self._serialized_chars(sections)
        if not sections and policy.included_sections and policy.max_serialized_chars:
            raise ContextBudgetExceededError(
                "context budget is too small for any allowed section"
            )
        return RoleContext(
            role_id=role.role_id.value,
            sections=sections,
            omitted_sections=CONTEXT_SECTIONS - sections.keys(),
            serialized_chars=serialized_chars,
            truncated=truncated,
        )

    project_context = project

    @staticmethod
    def _serialized_chars(sections: dict[str, Any]) -> int:
        return len(
            json.dumps(
                sections,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )


__all__ = [
    "ContextBudgetExceededError",
    "InvalidContextPolicyError",
    "RoleContextProjector",
]
