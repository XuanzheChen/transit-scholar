"""Deterministic validation performed before any action mutation."""

from __future__ import annotations

from pydantic import ValidationError

from transit_scholar.layer3.agent import RoleDefinition, RoleRegistry, UnregisteredRoleError

from .models import (
    ActionType,
    AdmitEvidenceAction,
    AgentAction,
    InvokeRoleAction,
    LinkEvidenceAction,
    RetrieveQueryAction,
    UpdateClaimAction,
    UpdateQueryAction,
)


class ActionValidationError(ValueError):
    """Raised before dispatch when an action crosses a deterministic boundary."""


class ActionValidator:
    def __init__(self, *, execution_service, ledger_service, role_registry: RoleRegistry) -> None:
        self.execution_service = execution_service
        self.ledger_service = ledger_service
        self.role_registry = role_registry

    def validate(self, action: AgentAction, role: RoleDefinition) -> AgentAction:
        if action.action_type.value not in role.allowed_actions:
            raise ActionValidationError(
                f"Role {role.role_id.value!r} may not perform {action.action_type.value}"
            )

        run = self._read(lambda: self.execution_service.get_agent_run(action.agent_run_id))
        if run.workspace_id != action.workspace_id:
            raise ActionValidationError("action workspace does not own the agent run")
        self._read(
            lambda: self.execution_service.get_research_session(
                action.agent_run_id, action.research_session_id
            )
        )

        if isinstance(action, UpdateQueryAction | RetrieveQueryAction):
            self._query(action.research_session_id, action.query_id)
        elif isinstance(action, AdmitEvidenceAction):
            self._query(action.research_session_id, action.source_query_id)
            if action.evidence.locator.workspace_id != action.workspace_id:
                raise ActionValidationError("evidence belongs to a different workspace")
        elif isinstance(action, UpdateClaimAction):
            if action.status is None and action.rationale is None:
                raise ActionValidationError("UPDATE_CLAIM requires status or rationale")
            self._claim(action.research_session_id, action.claim_id)
        elif isinstance(action, LinkEvidenceAction):
            self._claim(action.research_session_id, action.claim_id)
            self._evidence(action.research_session_id, action.evidence_id)
        elif isinstance(action, InvokeRoleAction):
            try:
                target = self.role_registry.get(action.target_role_id)
                target.input_contract.model_validate(action.role_input)
            except (UnregisteredRoleError, ValidationError, ValueError) as exc:
                raise ActionValidationError(f"invalid role invocation: {exc}") from exc
        elif isinstance(action, RetrieveQueryAction):  # pragma: no cover - guarded above
            pass

        if isinstance(action, RetrieveQueryAction) and action.tool_name not in role.allowed_tools:
            raise ActionValidationError(
                f"Role {role.role_id.value!r} may not use tool {action.tool_name!r}"
            )
        return action

    def _query(self, session_id: str, query_id: str) -> None:
        self._read(lambda: self.ledger_service.get_query(
            research_session_id=session_id, query_id=query_id
        ))

    def _claim(self, session_id: str, claim_id: str) -> None:
        self._read(lambda: self.ledger_service.get_claim(
            research_session_id=session_id, claim_id=claim_id
        ))

    def _evidence(self, session_id: str, evidence_id: str) -> None:
        self._read(lambda: self.ledger_service.get_evidence(
            research_session_id=session_id, evidence_id=evidence_id
        ))

    @staticmethod
    def _read(operation):
        try:
            return operation()
        except ActionValidationError:
            raise
        except Exception as exc:
            raise ActionValidationError(str(exc)) from exc


__all__ = ["ActionValidationError", "ActionValidator"]
