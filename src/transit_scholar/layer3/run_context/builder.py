"""Deterministic run observation and bounded cross-session handoff projection."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from transit_scholar.layer3.memory import EpisodicMemoryCandidate

from .models import RunContextSnapshot, RunOrchestrationState, RunRuntimeConfig, SessionHandoffContext, SessionOutcome


class RunContextSnapshotBuilder:
    """Build an AgentRun-scoped snapshot from authoritative read models."""

    def build(self, *, agent_run: Any, session_outcomes: Iterable[SessionOutcome] = (), research_plan: Any | None = None,
              active_session_ids: Iterable[str] = (), failed_session_ids: Iterable[str] = (), claim_refs: Iterable[str] = (),
              unresolved_items: Iterable[str] = (), conflicting_items: Iterable[str] = (), orchestration_state: RunOrchestrationState | None = None,
              episodic_memory: Iterable[EpisodicMemoryCandidate] = ()) -> RunContextSnapshot:
        run = self._dump(agent_run)
        goal = run.get("user_goal") or run.get("goal")
        run_id = run.get("agent_run_id") or run.get("id")
        if not goal or not run_id:
            raise ValueError("agent_run must provide agent_run_id and user_goal")
        outcomes = list(session_outcomes)
        failed = list(failed_session_ids) or [o.research_session_id for o in outcomes if o.status != "completed"]
        memory = list(episodic_memory)
        workspace_id = run.get("workspace_id")
        if memory:
            if workspace_id is None:
                raise ValueError(
                    "AgentRun workspace is required when episodic memory is supplied"
                )
            if any(
                candidate.workspace_id != str(workspace_id) for candidate in memory
            ):
                raise ValueError("episodic memory workspace does not match AgentRun")
        return RunContextSnapshot(agent_run_id=str(run_id), user_goal=str(goal), research_plan=research_plan,
            session_outcomes=outcomes, active_session_ids=list(active_session_ids), failed_session_ids=failed,
            claim_refs=list(claim_refs) or [r for o in outcomes for r in o.claim_refs], unresolved_items=list(unresolved_items),
            conflicting_items=list(conflicting_items), orchestration_state=orchestration_state,
            episodic_memory=memory)

    @staticmethod
    def _dump(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"): return value.model_dump(mode="json")
        if isinstance(value, Mapping): return dict(value)
        return {"agent_run_id": getattr(value, "agent_run_id", getattr(value, "id", None)), "user_goal": getattr(value, "user_goal", None)}


class SessionHandoffProjector:
    """Select bounded structured research results; never copies execution history."""

    def project(self, snapshot: RunContextSnapshot, *, current_research_question: str, config: RunRuntimeConfig | None = None) -> SessionHandoffContext:
        cfg = config or RunRuntimeConfig()
        prior = [o for o in snapshot.session_outcomes if o.status == "completed"][-cfg.max_prior_sessions:]
        summaries = [(o.final_summary or o.final_response or o.research_question)[:cfg.max_serialized_chars] for o in prior]
        claims = [c for o in prior for c in o.key_claims[:cfg.max_claims_per_session]]
        provenance = [r for o in prior for r in (*o.claim_refs, *o.evidence_refs, *o.source_refs)]
        items = list(snapshot.unresolved_items) + list(snapshot.conflicting_items)
        budget = cfg.max_handoff_items
        summaries = summaries[:budget]
        remaining = max(0, budget - len(summaries))
        claims = claims[:remaining]
        remaining = max(0, remaining - len(claims))
        provenance = provenance[:remaining]
        remaining = max(0, remaining - len(provenance))
        items = items[:remaining]
        result = SessionHandoffContext(run_goal=snapshot.user_goal, current_research_question=current_research_question,
            prior_session_summaries=summaries, relevant_prior_claims=claims, unresolved_or_conflicting_items=items,
            provenance_refs=provenance)
        if len(result.model_dump_json()) > cfg.max_serialized_chars:
            required = SessionHandoffContext(run_goal=snapshot.user_goal, current_research_question=current_research_question)
            if len(required.model_dump_json()) > cfg.max_serialized_chars:
                raise ValueError("max_serialized_chars is too small for required handoff content")
        while len(result.model_dump_json()) > cfg.max_serialized_chars and (result.provenance_refs or result.relevant_prior_claims or result.prior_session_summaries or result.unresolved_or_conflicting_items):
            if result.provenance_refs: result.provenance_refs.pop()
            elif result.relevant_prior_claims: result.relevant_prior_claims.pop()
            elif result.prior_session_summaries: result.prior_session_summaries.pop()
            else: result.unresolved_or_conflicting_items.pop()
        if len(result.model_dump_json()) > cfg.max_serialized_chars:
            raise ValueError("max_serialized_chars is too small for handoff content")
        return result

    project_handoff = project


__all__ = ["RunContextSnapshotBuilder", "SessionHandoffProjector"]
