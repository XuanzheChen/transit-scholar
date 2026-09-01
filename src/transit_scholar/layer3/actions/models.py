"""Structured action contracts for the Layer3 Stage5 runtime gateway."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.agent import RoleId
from transit_scholar.layer3.evidence import ResearchEvidence


class ActionType(StrEnum):
    CREATE_QUERY = "CREATE_QUERY"
    UPDATE_QUERY = "UPDATE_QUERY"
    RETRIEVE_QUERY = "RETRIEVE_QUERY"
    ADMIT_EVIDENCE = "ADMIT_EVIDENCE"
    CREATE_CLAIM = "CREATE_CLAIM"
    UPDATE_CLAIM = "UPDATE_CLAIM"
    LINK_EVIDENCE = "LINK_EVIDENCE"
    INVOKE_ROLE = "INVOKE_ROLE"
    FINISH_SESSION = "FINISH_SESSION"


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_type: ActionType
    workspace_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    research_session_id: str = Field(min_length=1)


class CreateQueryAction(ActionBase):
    action_type: Literal[ActionType.CREATE_QUERY] = ActionType.CREATE_QUERY
    query_id: str | None = Field(default=None, min_length=1)
    query_text: str = Field(min_length=1)
    parent_query_id: str | None = Field(default=None, min_length=1)


class UpdateQueryAction(ActionBase):
    action_type: Literal[ActionType.UPDATE_QUERY] = ActionType.UPDATE_QUERY
    query_id: str = Field(min_length=1)
    status: Literal["active", "completed", "abandoned"]


class RetrieveQueryAction(ActionBase):
    action_type: Literal[ActionType.RETRIEVE_QUERY] = ActionType.RETRIEVE_QUERY
    query_id: str = Field(min_length=1)
    tool_name: Literal["retrieve_knowledge"] = "retrieve_knowledge"


class AdmitEvidenceAction(ActionBase):
    action_type: Literal[ActionType.ADMIT_EVIDENCE] = ActionType.ADMIT_EVIDENCE
    source_query_id: str = Field(min_length=1)
    evidence: ResearchEvidence


class CreateClaimAction(ActionBase):
    action_type: Literal[ActionType.CREATE_CLAIM] = ActionType.CREATE_CLAIM
    claim_id: str | None = Field(default=None, min_length=1)
    statement: str = Field(min_length=1)
    status: Literal["proposed", "supported", "conflicting", "rejected"] = "proposed"
    rationale: str | None = Field(default=None, min_length=1)


class UpdateClaimAction(ActionBase):
    action_type: Literal[ActionType.UPDATE_CLAIM] = ActionType.UPDATE_CLAIM
    claim_id: str = Field(min_length=1)
    status: Literal["proposed", "supported", "conflicting", "rejected"] | None = None
    rationale: str | None = Field(default=None, min_length=1)


class LinkEvidenceAction(ActionBase):
    action_type: Literal[ActionType.LINK_EVIDENCE] = ActionType.LINK_EVIDENCE
    claim_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    relation: Literal["supports", "contradicts"]


class InvokeRoleAction(ActionBase):
    action_type: Literal[ActionType.INVOKE_ROLE] = ActionType.INVOKE_ROLE
    target_role_id: RoleId
    role_input: dict[str, Any]


class FinishSessionAction(ActionBase):
    action_type: Literal[ActionType.FINISH_SESSION] = ActionType.FINISH_SESSION
    status: Literal["completed", "failed", "cancelled"] = "completed"


AgentAction = Annotated[
    CreateQueryAction
    | UpdateQueryAction
    | RetrieveQueryAction
    | AdmitEvidenceAction
    | CreateClaimAction
    | UpdateClaimAction
    | LinkEvidenceAction
    | InvokeRoleAction
    | FinishSessionAction,
    Field(discriminator="action_type"),
]


class ActionExecutionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    action_type: ActionType
    value: Any = None


__all__ = [name for name in globals() if not name.startswith("_")]
