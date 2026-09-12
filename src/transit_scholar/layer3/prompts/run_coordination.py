"""Prompt construction for semantic run-level coordination."""

from __future__ import annotations

import json

from transit_scholar.layer3.run_context import (
    RunContextSnapshot,
    RunCoordinatorContext,
    RunCoordinatorContextProjector,
)


def build_run_coordination_prompt(
    context: RunCoordinatorContext | RunContextSnapshot,
) -> str:
    """Build a prompt from bounded run-level research-result context.

    Accepting a snapshot keeps this prompt helper backward compatible for
    direct callers while still applying the same projection boundary.
    """
    projected = (
        context
        if isinstance(context, RunCoordinatorContext)
        else RunCoordinatorContextProjector().project(context)
    )
    payload = projected.model_dump(mode="json")
    return (
        "Choose the next action for this research run. Return only a JSON object "
        "matching RunDecision. Use direct_session for one focused session, "
        "planned_research when multiple or staged questions are needed, and "
        "complete only when the existing research is sufficient. Preserve the "
        "provided run state and do not create agents or roles. Reason from "
        "Session summaries, Claims, references, gaps, and plan state.\n"
        "Plan sequencing is strict: when the run has no research plan yet, "
        "propose it with proposed_questions and leave plan_item_updates empty. "
        "Use plan_item_updates or abandon_item_ids only with item_id values that "
        "already appear in the plan state, and only with mode=planned_research; "
        "an update for an item_id that is not in the plan is rejected. When a "
        "plan has pending items, return mode=planned_research so the runtime can "
        "execute the next pending item; do not return direct_session to advance "
        "an existing plan. A direct_session must contain exactly one focused "
        "question in proposed_questions. Return mode=complete only when every "
        "existing plan item is completed, failed, or abandoned. Put all decision "
        "fields at the top level; metadata is explanatory only and cannot carry "
        "action, research_question, plan_item_updates, or abandon_item_ids.\n"
        "Use exactly this top-level JSON shape: {\"mode\": \"direct_session|"
        "planned_research|complete\", \"proposed_questions\": [], "
        "\"plan_item_updates\": [], \"abandon_item_ids\": [], "
        "\"completion_reason\": null, \"metadata\": {}}.\n"
        f"RunCoordinatorContext:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


__all__ = ["build_run_coordination_prompt"]
