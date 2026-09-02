"""Prompt construction for semantic run-level coordination."""

from __future__ import annotations

import json

from transit_scholar.layer3.run_context import RunContextSnapshot


def build_run_coordination_prompt(snapshot: RunContextSnapshot) -> str:
    """Build a bounded prompt for selecting the next run-level action."""
    payload = snapshot.model_dump(mode="json")
    return (
        "Choose the next action for this research run. Return only a JSON object "
        "matching RunDecision. Use direct_session for one focused session, "
        "planned_research when multiple or staged questions are needed, and "
        "complete only when the existing research is sufficient. Preserve the "
        "provided run state and do not create agents or roles.\n"
        f"RunContextSnapshot:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


__all__ = ["build_run_coordination_prompt"]
