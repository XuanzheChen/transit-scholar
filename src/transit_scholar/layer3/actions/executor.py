"""Thin deterministic dispatcher into existing Layer3 service boundaries."""

from __future__ import annotations

from collections.abc import Callable

from transit_scholar.layer3.agent import RoleDefinition
from transit_scholar.layer3.retrieval import ResearchQuery

from .models import (
    ActionExecutionResult,
    AdmitEvidenceAction,
    AgentAction,
    CreateClaimAction,
    CreateQueryAction,
    FinishSessionAction,
    InvokeRoleAction,
    LinkEvidenceAction,
    RetrieveQueryAction,
    UpdateClaimAction,
    UpdateQueryAction,
)
from .validation import ActionValidator


class ActionExecutor:
    """Validate first, then make exactly one lower-layer delegation."""

    def __init__(
        self,
        *,
        validator: ActionValidator,
        execution_service,
        ledger_service,
        knowledge_service,
        role_invoker: Callable[[object, dict], object],
    ) -> None:
        self.validator = validator
        self.execution_service = execution_service
        self.ledger_service = ledger_service
        self.knowledge_service = knowledge_service
        self.role_invoker = role_invoker

    def execute(self, action: AgentAction, role: RoleDefinition) -> ActionExecutionResult:
        self.validator.validate(action, role)
        value = self._dispatch(action)
        return ActionExecutionResult(action_type=action.action_type, value=value)

    def _dispatch(self, action: AgentAction):
        common = {"research_session_id": action.research_session_id}
        if isinstance(action, CreateQueryAction):
            return self.ledger_service.create_query(
                **common,
                query_id=action.query_id,
                query_text=action.query_text,
                parent_query_id=action.parent_query_id,
            )
        if isinstance(action, UpdateQueryAction):
            return self.ledger_service.update_query_status(
                **common, query_id=action.query_id, status=action.status
            )
        if isinstance(action, RetrieveQueryAction):
            query = self.ledger_service.get_query(**common, query_id=action.query_id)
            return self.knowledge_service.retrieve_knowledge(
                ResearchQuery(
                    query_id=query.query_id,
                    session_id=action.research_session_id,
                    workspace_id=action.workspace_id,
                    query_text=query.query_text,
                )
            )
        if isinstance(action, AdmitEvidenceAction):
            return self.ledger_service.admit_evidence(
                **common, source_query_id=action.source_query_id, evidence=action.evidence
            )
        if isinstance(action, CreateClaimAction):
            return self.ledger_service.create_claim(
                **common,
                claim_id=action.claim_id,
                statement=action.statement,
                status=action.status,
                rationale=action.rationale,
            )
        if isinstance(action, UpdateClaimAction):
            return self.ledger_service.update_claim(
                **common, claim_id=action.claim_id, status=action.status, rationale=action.rationale
            )
        if isinstance(action, LinkEvidenceAction):
            return self.ledger_service.link_evidence_to_claim(
                **common,
                claim_id=action.claim_id,
                evidence_id=action.evidence_id,
                relation=action.relation,
            )
        if isinstance(action, InvokeRoleAction):
            target = self.validator.role_registry.get(action.target_role_id)
            return self.role_invoker(target, action.role_input)
        if isinstance(action, FinishSessionAction):
            return self.execution_service.update_research_session_status(
                action.agent_run_id, action.research_session_id, action.status
            )
        raise TypeError(f"Unsupported action: {type(action).__name__}")


__all__ = ["ActionExecutor"]
