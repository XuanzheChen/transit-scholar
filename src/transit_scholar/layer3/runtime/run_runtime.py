"""Sequential L3S6 run-level orchestration over the L3S5 boundary."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from transit_scholar.layer3.planning import ResearchPlan, ResearchPlanItem, RunDecision
from transit_scholar.layer3.run_context import (
    RunContextSnapshotBuilder, RunFinalResponseArtifact, RunOrchestrationState,
    RunRuntimeConfig, SessionHandoffProjector, SessionOutcome,
)


class RunOrchestrationConfigurationError(RuntimeError):
    """A run cannot create or start its authoritative research session."""


class RunResearchRuntime:
    """Coordinate sessions one at a time; session behavior remains L3S5-owned."""

    def __init__(self, *, session_runtime: Any, coordinator: Callable[[Any], Any],
                 synthesis: Callable[[Any], Any] | None = None, session_factory: Callable[..., Any] | None = None,
                 execution_service: Any | None = None, ledger_service: Any | None = None,
                 config: RunRuntimeConfig | None = None, snapshot_builder: Any | None = None,
                 handoff_projector: Any | None = None, trace: Any | None = None,
                 is_cancelled: Callable[[], bool] | None = None, state_store: Any | None = None):
        self.session_runtime, self.coordinator, self.synthesis = session_runtime, coordinator, synthesis
        self.execution_service, self.ledger_service = execution_service, ledger_service
        self.session_factory = session_factory or self._default_session
        self.config = config or RunRuntimeConfig()
        self.snapshot_builder = snapshot_builder or RunContextSnapshotBuilder()
        self.handoff_projector = handoff_projector or SessionHandoffProjector()
        self.trace, self.is_cancelled, self.state_store = trace, is_cancelled or (lambda: False), state_store

    def execute(self, *, agent_run_id: str, user_goal: str | None = None, agent_run: Any | None = None) -> dict[str, Any]:
        run = agent_run or {"agent_run_id": agent_run_id, "user_goal": user_goal or ""}
        state, outcomes, plan = self._load(agent_run_id)
        if state.current_research_session_id:
            state, outcomes, plan = self._recover_current(run, state, outcomes, plan)
        state.status = "running"
        self._persist(state, outcomes, plan)
        self._event(agent_run_id, "run.started", {})
        while True:
            state.run_steps += 1
            reason = self._limit(state, outcomes)
            if reason: return self._result(state, outcomes, plan, reason)
            snapshot = self.snapshot_builder.build(agent_run=run, session_outcomes=outcomes, research_plan=plan, orchestration_state=state)
            decision = self._validate(self.coordinator(snapshot))
            self._event(agent_run_id, "run.coordination", {"mode": decision.mode})
            if decision.mode == "complete":
                self._event(agent_run_id, "run.synthesis.started", {})
                artifact = self.synthesis(snapshot) if self.synthesis else RunFinalResponseArtifact(answer_text=decision.completion_reason or "", contributing_session_ids=[o.research_session_id for o in outcomes], completion_reason=decision.completion_reason)
                if not isinstance(artifact, RunFinalResponseArtifact):
                    artifact = RunFinalResponseArtifact.model_validate(artifact)
                artifact = artifact.model_copy(update={
                    "status": "completed",
                    "completion_reason": decision.completion_reason or artifact.completion_reason,
                })
                return self._result(state, outcomes, plan, "semantic_completion", artifact)
            if decision.mode == "planned_research":
                state.planning_round += 1
                if state.planning_round > self.config.max_planning_rounds:
                    return self._result(state, outcomes, plan, "max_planning_rounds")
                if decision.plan_item_updates and plan is None:
                    raise ValueError("plan_item_updates require an existing research plan")
                if plan is not None:
                    known_item_ids = {item.item_id for item in plan.items}
                    unknown_item_ids = {
                        update.item_id for update in decision.plan_item_updates
                        if update.item_id not in known_item_ids
                    }
                    if unknown_item_ids:
                        raise ValueError(
                            f"unknown plan item update IDs: {sorted(unknown_item_ids)}"
                        )
                if plan is None:
                    plan = ResearchPlan(plan_id=uuid4().hex, agent_run_id=agent_run_id, planning_round=state.planning_round)
                    state.research_plan_id = plan.plan_id
                    self._event(agent_run_id, "run.plan.created", {"plan_id": plan.plan_id})
                    if outcomes:
                        self._event(agent_run_id, "run.promoted_to_planned", {"plan_id": plan.plan_id})
                for q in decision.proposed_questions:
                    plan.items.append(ResearchPlanItem(item_id=uuid4().hex, research_question=q, order=len(plan.items)))
                for update in decision.plan_item_updates:
                    existing = next((item for item in plan.items if item.item_id == update.item_id), None)
                    replacement = existing.model_copy(update=update.model_dump(exclude_unset=True))
                    plan.items[plan.items.index(existing)] = replacement
                for item_id in decision.abandon_item_ids:
                    existing = next((item for item in plan.items if item.item_id == item_id), None)
                    if existing is not None and existing.status in {"pending", "running"}:
                        existing.status = "abandoned"
                self._event(agent_run_id, "run.plan.updated", {"plan_id": plan.plan_id})
                self._event(agent_run_id, "run.replan", {"plan_id": plan.plan_id, "planning_round": state.planning_round})
                pending = next((i for i in sorted(plan.items, key=lambda i:i.order) if i.status == "pending"), None)
                if pending is None: continue
                pending.status = "running"; state.current_plan_item_id = pending.item_id; question = pending.research_question; sid = pending.research_session_id or uuid4().hex; pending.research_session_id = sid
            else:
                question, sid = (decision.proposed_questions[0] if decision.proposed_questions else snapshot.user_goal), uuid4().hex
            if len(outcomes) >= self.config.max_sessions:
                return self._result(state, outcomes, plan, "max_sessions")
            if self._requires_execution_service() and self.execution_service is None:
                raise RunOrchestrationConfigurationError(
                    "execution_service is required for L3S5 session execution"
                )
            handoff = self.handoff_projector.project(snapshot, current_research_question=question, config=self.config)
            try:
                session = self._create_session(agent_run_id=agent_run_id, research_session_id=sid, research_question=question, handoff_context=handoff)
            except Exception as exc:
                raise RunOrchestrationConfigurationError(
                    f"failed to create research session {sid}: {exc}"
                ) from exc
            try:
                state.current_research_session_id = sid
                self._event(agent_run_id, "run.session.created", {"research_session_id": sid, "research_question": question})
                self._persist(state, outcomes, plan)
                self._event(agent_run_id, "run.session.started", {"research_session_id": sid}, sid)
                if hasattr(self.session_runtime, "execute"):
                    raw = self.session_runtime.execute(agent_run_id=agent_run_id, research_session_id=sid, session_handoff=handoff)
                else:
                    raw = self.session_runtime(session, handoff)
                outcome = self._adapt_outcome(raw, sid, question)
            except Exception as exc:
                outcome = SessionOutcome(research_session_id=sid, research_question=question, status="failed", failure_reason=str(exc))
            outcomes.append(outcome); state.current_research_session_id = None; state.current_plan_item_id = None
            if outcome.status == "completed": state.completed_session_ids.append(sid)
            else: state.failed_session_ids.append(sid)
            self._event(agent_run_id, "run.session.completed" if outcome.status == "completed" else "run.session.failed", {"research_session_id": sid, "status": outcome.status}, sid)
            if plan:
                item = next((i for i in plan.items if i.research_session_id == sid), None)
                if item: item.status = "completed" if outcome.status == "completed" else "failed"
            self._persist(state, outcomes, plan)

    run = execute
    coordinate = execute

    def _limit(self, state, outcomes):
        if self.is_cancelled(): return "cancelled"
        if state.run_steps > self.config.max_run_steps: return "max_run_steps"
        if len(outcomes) > self.config.max_sessions: return "max_sessions"
        if state.planning_round > self.config.max_planning_rounds: return "max_planning_rounds"
        if len(state.failed_session_ids) > self.config.max_failed_sessions: return "max_failed_sessions"

    def _recover_current(self, run, state, outcomes, plan):
        sid = state.current_research_session_id
        if any(o.research_session_id == sid for o in outcomes):
            state.current_research_session_id = None
            state.current_plan_item_id = None
            self._persist(state, outcomes, plan)
            return state, outcomes, plan
        question = None
        if plan:
            item = next((i for i in plan.items if i.research_session_id == sid), None)
            question = item.research_question if item else None
        if question is None and self.execution_service is not None:
            persisted = self.execution_service.get_research_session(state.agent_run_id, sid)
            question = getattr(persisted, "research_question", None)
            if question is None and isinstance(persisted, dict):
                question = persisted.get("research_question")
        question = question or "Resumed research session"
        snapshot = self.snapshot_builder.build(
            agent_run=run, session_outcomes=outcomes, research_plan=plan,
            orchestration_state=state,
        )
        handoff = self.handoff_projector.project(
            snapshot, current_research_question=question, config=self.config
        )
        try:
            if hasattr(self.session_runtime, "resume_session"):
                raw = self.session_runtime.resume_session(
                    agent_run_id=state.agent_run_id, research_session_id=sid,
                    session_handoff=handoff,
                )
            else:
                raw = self.session_runtime.execute(
                    agent_run_id=state.agent_run_id, research_session_id=sid,
                    session_handoff=handoff,
                )
            outcome = self._adapt_outcome(raw, sid, question)
        except Exception as exc:
            outcome = SessionOutcome(research_session_id=sid, research_question=question, status="failed", failure_reason=str(exc))
        outcomes.append(outcome)
        if outcome.status == "completed":
            if sid not in state.completed_session_ids: state.completed_session_ids.append(sid)
        else:
            if sid not in state.failed_session_ids: state.failed_session_ids.append(sid)
        if plan:
            item = next((i for i in plan.items if i.research_session_id == sid), None)
            if item: item.status = "completed" if outcome.status == "completed" else "failed"
        state.current_research_session_id = None
        state.current_plan_item_id = None
        self._persist(state, outcomes, plan)
        return state, outcomes, plan

    @staticmethod
    def _validate(value): return value if isinstance(value, RunDecision) else RunDecision.model_validate(value)
    def _create_session(self, **kwargs: Any) -> Any:
        if self.execution_service is not None:
            service = self.execution_service
            session = service.create_research_session(
                agent_run_id=kwargs["agent_run_id"], research_session_id=kwargs["research_session_id"],
                research_question=kwargs["research_question"],
            )
            persisted = service.get_research_session(kwargs["agent_run_id"], kwargs["research_session_id"])
            persisted_id = getattr(persisted, "research_session_id", None)
            persisted_question = getattr(persisted, "research_question", None)
            if isinstance(persisted, dict):
                persisted_id = persisted.get("research_session_id")
                persisted_question = persisted.get("research_question")
            if persisted_id != kwargs["research_session_id"] or persisted_question != kwargs["research_question"]:
                raise RuntimeError("authoritative L3S2 ResearchSession does not match selected Session")
            return session
        return self.session_factory(**kwargs)

    def _requires_execution_service(self) -> bool:
        """Identify the real L3S5 runtime without rejecting test doubles."""
        return self.session_runtime.__class__.__name__ == "MainResearchRuntime"

    def _adapt_outcome(self, raw: Any, sid: str, question: str) -> SessionOutcome:
        if isinstance(raw, SessionOutcome):
            return raw
        data = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else (raw if isinstance(raw, dict) else {})
        raw_status = data.get("status")
        status = {
            None: "completed",
            "completed": "completed",
            "succeeded": "completed",
            "success": "completed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "terminated": "terminated",
        }.get(raw_status, "failed")
        artifact = data.get("final_response")
        if hasattr(artifact, "model_dump"):
            artifact = artifact.model_dump(mode="python")
        if not isinstance(artifact, dict):
            artifact = {}
        claim_refs, evidence_refs = list(data.get("claim_refs", [])), list(data.get("evidence_refs", []))
        source_provenance: list[dict[str, Any]] = []
        if self.ledger_service is not None:
            claims = self.ledger_service.list_claims(research_session_id=sid)
            evidence = self.ledger_service.list_evidence(research_session_id=sid)
            claim_refs = [item.get("claim_id") if isinstance(item, dict) else item.claim_id for item in claims]
            evidence_refs = [item.get("evidence_id") if isinstance(item, dict) else item.evidence_id for item in evidence]
            source_provenance = [item if isinstance(item, dict) else item.model_dump(mode="python") for item in evidence]
        final_sources = artifact.get("source_references", [])
        for source in final_sources:
            if hasattr(source, "model_dump"):
                source = source.model_dump(mode="python")
            if isinstance(source, dict):
                source_provenance.append(source)
                evidence_id = source.get("evidence_id")
                if evidence_id and evidence_id not in evidence_refs:
                    evidence_refs.append(evidence_id)
        source_refs = [
            ref for ref in artifact.get(
                "source_refs",
                artifact.get("citation_references", data.get("source_refs", [])),
            ) if isinstance(ref, str)
        ]
        if not source_refs:
            source_refs = list(evidence_refs)
        answer = artifact.get("answer_text") if isinstance(artifact.get("answer_text"), str) else data.get("final_response") if isinstance(data.get("final_response"), str) else None
        return SessionOutcome(research_session_id=sid, research_question=question, status=status,
            final_response=answer, final_summary=artifact.get("final_summary") or artifact.get("termination_reason") or data.get("final_summary") or answer,
            claim_refs=claim_refs, evidence_refs=evidence_refs, source_refs=source_refs,
            source_provenance=source_provenance,
            failure_reason=data.get("failure_message"))
    @staticmethod
    def _default_session(**kwargs): return kwargs
    def _event(self, run_id, typ, payload, research_session_id=None):
        if self.trace and hasattr(self.trace, "append_event"):
            kwargs = {"agent_run_id": run_id, "event_type": typ, "payload": payload}
            if research_session_id is not None: kwargs["research_session_id"] = research_session_id
            self.trace.append_event(**kwargs)

    def _load(self, run_id):
        if not self.state_store: return RunOrchestrationState(agent_run_id=run_id, status="running"), [], None
        if hasattr(self.state_store, "load"):
            raw = self.state_store.load(run_id)
        elif hasattr(self.state_store, "load_state"):
            raw = self.state_store.load_state(agent_run_id=run_id)
        else:
            raw = self.state_store.get(run_id)
        if not raw: return RunOrchestrationState(agent_run_id=run_id, status="running"), [], None
        data = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else raw
        state = RunOrchestrationState.model_validate(data.get("orchestration_state", data))
        outcomes = [SessionOutcome.model_validate(o) for o in data.get("session_outcomes", data.get("outcomes", []))]
        plan_data = data.get("research_plan")
        return state, outcomes, ResearchPlan.model_validate(plan_data) if plan_data else None

    def _persist(self, state, outcomes, plan):
        if not self.state_store: return
        payload = {"orchestration_state": state.model_dump(mode="json"), "session_outcomes": [o.model_dump(mode="json") for o in outcomes], "research_plan": plan.model_dump(mode="json") if plan else None}
        if hasattr(self.state_store, "save"): self.state_store.save(state.agent_run_id, payload)
        elif hasattr(self.state_store, "save_state"):
            self.state_store.save_state(agent_run_id=state.agent_run_id, payload=payload)
        elif hasattr(self.state_store, "set"): self.state_store.set(state.agent_run_id, payload)
    def _result(self, state, outcomes, plan, reason, artifact=None):
        state.status = "completed" if reason == "semantic_completion" else ("cancelled" if reason == "cancelled" else "terminated"); state.termination_reason = reason
        self._persist(state, outcomes, plan)
        self._event(state.agent_run_id, "run.completed" if state.status == "completed" else "run.failed", {"reason": reason})
        return {"status": state.status, "termination_reason": reason, "outcomes": outcomes, "session_outcomes": outcomes, "research_plan": plan, "orchestration_state": state, "final_response": artifact}
