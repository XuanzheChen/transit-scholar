"""Framework-neutral contracts for hybrid retrieval planning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.retrieval import ResearchQuery, RetrievalDiagnostic, RetrievalStrategy


SourceKind = Literal["schema", "wiki", "rag"]


class RetrievalCapabilities(BaseModel):
    """Bounded, non-content capability summary supplied to the LLM planner."""

    model_config = ConfigDict(extra="forbid")

    available_sources: set[SourceKind] = Field(default_factory=set)
    available_tools: set[str] = Field(default_factory=set)
    schema_field_ids: set[str] = Field(default_factory=set)
    eligible_paper_ids: set[str] = Field(default_factory=set)
    l2s1_ready_paper_ids: set[str] = Field(default_factory=set)
    schema_ready_paper_ids: set[str] = Field(default_factory=set)
    wiki_ready: bool = False
    max_actions: int = Field(default=8, ge=1)
    max_action_limit: int = Field(default=20, ge=1)
    workspace_revision: int | None = Field(default=None, ge=1)


class RetrievalContext(BaseModel):
    """The query and bounded current-Workspace capabilities for planning."""

    model_config = ConfigDict(extra="forbid")

    query: ResearchQuery
    capabilities: RetrievalCapabilities


class PlanningResult(BaseModel):
    """The planner outcome; invalid strategies are never executable."""

    model_config = ConfigDict(extra="forbid")

    strategy: RetrievalStrategy | None = None
    diagnostics: list[RetrievalDiagnostic] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.strategy is not None and not any(
            diagnostic.status == "failed" for diagnostic in self.diagnostics
        )
