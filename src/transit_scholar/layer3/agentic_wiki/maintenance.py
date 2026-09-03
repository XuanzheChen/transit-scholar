"""Deterministic provenance maintenance for Agentic Wiki entries."""
from __future__ import annotations

from typing import Any, Callable

from .store import AgenticWikiStore


class AgenticWikiMaintenance:
    """Runs one lightweight, Workspace-scoped health pass before Session use."""

    def __init__(self, store: AgenticWikiStore, *, claims: Any = None,
                 evidence: Any = None, papers: Any = None) -> None:
        self.store = store
        self.claims = claims
        self.evidence = evidence
        self.papers = papers
        self.calls = 0

    def __call__(self, workspace_id: str):
        self.calls += 1
        return self.store.maintain(workspace_id, claims=self._resolve(self.claims, workspace_id),
                                    evidence=self._resolve(self.evidence, workspace_id),
                                    papers=self._resolve(self.papers, workspace_id))

    @staticmethod
    def _resolve(source: Any, workspace_id: str) -> Any:
        if source is None:
            return None
        if callable(source):
            return source(workspace_id)
        return source


def maintain_before_session_use(store: AgenticWikiStore, workspace_id: str, **resolvers: Any):
    """Convenience boundary; never invokes an LLM or mutates other Workspaces."""
    return AgenticWikiMaintenance(store, **resolvers)(workspace_id)


__all__ = ["AgenticWikiMaintenance", "maintain_before_session_use"]
