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
        f"RunCoordinatorContext:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


__all__ = ["build_run_coordination_prompt"]
