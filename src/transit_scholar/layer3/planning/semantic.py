"""Semantic decision-maker adapters for run-level planning."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from transit_scholar.layer3.planning.models import RunDecision
from transit_scholar.layer3.prompts import build_run_coordination_prompt
from transit_scholar.layer3.run_context import RunContextSnapshot


def resolve_runtime_llm_client(config: Any | None = None) -> Any:
    """Resolve the shared configured LLM client at the runtime boundary."""
    from transit_scholar.layer2.schema_extraction.llm import (
        resolve_runtime_llm_client as resolve,
    )

    return resolve(config)


class StructuredRunSemanticDecider:
    """Use a structured LLM client to produce validated run decisions.

    Client resolution is lazy when no client is injected. This keeps the
    composition constructible in offline environments while preserving an
    explicit provider failure when a production run actually needs a decision.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        llm_config: Any | None = None,
        client_resolver: Callable[[Any | None], Any] = resolve_runtime_llm_client,
    ) -> None:
        self._client = client
        self.llm_config = llm_config
        self._client_resolver = client_resolver

    @property
    def client(self) -> Any | None:
        """Return an injected client without forcing lazy production resolution."""
        return self._client

    def decide(self, snapshot: RunContextSnapshot) -> RunDecision:
        client = self._client
        if client is None:
            client = self._client_resolver(self.llm_config)
            self._client = client
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the governed semantic coordinator for one research "
                    "run. Return only schema-valid RunDecision JSON."
                ),
            },
            {"role": "user", "content": build_run_coordination_prompt(snapshot)},
        ]
        raw = client.generate_structured(
            messages,
            RunDecision,
            {"prompt_key": "run_coordinator", "agent_run_id": snapshot.agent_run_id},
        )
        return RunDecision.model_validate(raw)

    def __call__(self, snapshot: RunContextSnapshot) -> RunDecision:
        return self.decide(snapshot)


LLMRunSemanticDecider = StructuredRunSemanticDecider


__all__ = [
    "LLMRunSemanticDecider",
    "StructuredRunSemanticDecider",
    "resolve_runtime_llm_client",
]
