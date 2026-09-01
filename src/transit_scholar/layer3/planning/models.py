"""Framework-neutral, schema-validated run planning value objects."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PlanItemStatus = Literal["pending", "running", "completed", "abandoned", "failed"]
DecisionMode = Literal["direct_session", "planned_research", "complete"]


class ResearchPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    order: int = Field(ge=0)
    status: PlanItemStatus = "pending"
    research_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    items: list[ResearchPlanItem] = Field(default_factory=list)
    planning_round: int = Field(default=0, ge=0)


class RunDecision(BaseModel):
    """Validated semantic output from the predefined run coordinator."""

    model_config = ConfigDict(extra="forbid")

    mode: DecisionMode
    proposed_questions: list[str] = Field(default_factory=list)
    plan_item_updates: list[ResearchPlanItem] = Field(default_factory=list)
    abandon_item_ids: list[str] = Field(default_factory=list)
    completion_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
