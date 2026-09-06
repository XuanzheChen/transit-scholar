"""Production Role policy and deterministic action planning adapters."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from collections.abc import Mapping

from pydantic import BaseModel

from transit_scholar.layer2.schema_extraction.llm import StructuredLLMClient, resolve_runtime_llm_client
from transit_scholar.layer3.actions.models import (
    AdmitEvidenceAction, CreateClaimAction, CreateQueryAction, LinkEvidenceAction,
    RetrieveQueryAction,
)
from transit_scholar.layer3.agent import (
    ClaimReasoningOutput, EvidenceReasoningOutput, QueryPlanningOutput, RoleDefinition,
    RoleId, StructuredOutputRepairContext,
)
from transit_scholar.layer3.context import RoleContext
from transit_scholar.layer3.evidence import ResearchEvidence


class StructuredLLMRolePolicy:
    """RolePolicy backed by the repository's unified structured LLM client."""

    def __init__(self, llm_client: StructuredLLMClient | None = None) -> None:
        self.llm_client = llm_client or resolve_runtime_llm_client()

    def decide(self, definition, role_input, state, role_context, repair_context=None):
        if not isinstance(role_context, RoleContext):
            raise TypeError("role_context must be a projected RoleContext")
        payload = {
            "role_input": role_input.model_dump(mode="json"),
            "role_context": role_context.model_dump(mode="json"),
            "working_state": state.model_dump(mode="json"),
        }
        if repair_context is not None:
            payload["structured_output_repair"] = repair_context.model_dump(mode="json")
        messages = [
            {"role": "system", "content": definition.prompt_template},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
        ]
        return self.llm_client.generate_structured(
            messages,
            definition.output_contract,
            metadata={
                "role_id": definition.role_id.value,
                "prompt_key": definition.role_id.value,
                "repair_attempt": getattr(repair_context, "attempt", 0),
            },
        )


class BuiltinRoleActionPlanner:
    """Pure deterministic conversion of built-in semantic outputs to actions."""

    def __call__(self, definition, output, role_context):
        return self.plan(definition, output, role_context)

    def plan(
        self,
        definition: RoleDefinition,
        output: BaseModel | Mapping[str, object],
        role_context: RoleContext,
    ) -> tuple[object, ...]:
        if not isinstance(role_context, RoleContext):
            raise TypeError("role_context must be a projected RoleContext")
        if role_context.role_id != definition.role_id.value:
            raise ValueError("role context does not belong to the requested Role")

        validated_output = definition.output_contract.model_validate(output)
        if definition.role_id == RoleId.QUERY_PLANNING:
            return self._queries(QueryPlanningOutput.model_validate(validated_output), role_context)
        if definition.role_id == RoleId.EVIDENCE_REASONING:
            return self._evidence(EvidenceReasoningOutput.model_validate(validated_output), role_context)
        if definition.role_id == RoleId.CLAIM_REASONING:
            return self._claims(ClaimReasoningOutput.model_validate(validated_output), role_context)
        return ()

    @staticmethod
    def _ids(context: RoleContext) -> tuple[str, str, str]:
        session = context.sections.get("session", {})
        if hasattr(session, "model_dump"):
            session = session.model_dump(mode="json")
        if not isinstance(session, dict):
            session = {}
        run = session.get("agent_run", {}) or {}
        research = session.get("research_session", {}) or {}
        workspace = context.sections.get("workspace", {})
        if hasattr(workspace, "model_dump"):
            workspace = workspace.model_dump(mode="json")
        if not isinstance(workspace, dict):
            workspace = {}
        workspace_id = workspace.get("workspace_id") or run.get("workspace_id", "")
        run_id = run.get("agent_run_id", run.get("id", ""))
        session_id = research.get("research_session_id", research.get("id", ""))
        if not all((workspace_id, run_id, session_id)):
            raise ValueError("role context is missing action ownership identifiers")
        return str(workspace_id), str(run_id), str(session_id)

    def _queries(self, output, context):
        workspace_id, run_id, session_id = self._ids(context)
        actions = []
        for index, text in enumerate(output.proposed_queries):
            query = str(text).strip()
            if not query:
                continue
            query_id = uuid5(NAMESPACE_URL, f"{session_id}:query:{index}:{query}").hex
            common = dict(workspace_id=workspace_id, agent_run_id=run_id, research_session_id=session_id)
            actions.extend((CreateQueryAction(**common, query_id=query_id, query_text=query), RetrieveQueryAction(**common, query_id=query_id)))
        return tuple(actions)

    def _evidence(self, output, context):
        workspace_id, run_id, session_id = self._ids(context)
        available = context.sections.get("retrieved_evidence", ())
        by_id = {}
        for item in available:
            if hasattr(item, "evidence_id"):
                by_id[item.evidence_id] = item.payload
            elif isinstance(item, dict):
                by_id[item.get("evidence_id")] = item.get("payload", item)
        actions = []
        for evidence_id in output.admitted_evidence_ids:
            payload = by_id.get(evidence_id)
            if payload is None:
                raise ValueError(f"admitted evidence {evidence_id!r} was not retrieved")
            evidence = ResearchEvidence.model_validate(payload)
            if evidence.evidence_id != evidence_id:
                raise ValueError(f"retrieved evidence {evidence_id!r} has mismatched payload identity")
            if evidence.query_provenance is None:
                raise ValueError(f"retrieved evidence {evidence_id!r} has no query provenance")
            actions.append(AdmitEvidenceAction(workspace_id=workspace_id, agent_run_id=run_id, research_session_id=session_id, source_query_id=evidence.query_provenance.query_id, evidence=evidence))
        return tuple(actions)

    def _claims(self, output, context):
        workspace_id, run_id, session_id = self._ids(context)
        admitted = context.sections.get("accepted_evidence", ())
        admitted_ids = {getattr(item, "evidence_id", item.get("evidence_id") if isinstance(item, dict) else None) for item in admitted}
        actions = []
        for index, proposal in enumerate(output.proposed_claims):
            evidence_ids = [eid for eid in proposal.evidence_ids if eid in admitted_ids]
            if len(evidence_ids) != len(proposal.evidence_ids):
                raise ValueError("claim references evidence that is not admitted")
            claim_id = uuid5(NAMESPACE_URL, f"{session_id}:claim:{index}:{proposal.statement}").hex
            common = dict(workspace_id=workspace_id, agent_run_id=run_id, research_session_id=session_id)
            actions.append(CreateClaimAction(**common, claim_id=claim_id, statement=proposal.statement))
            actions.extend(LinkEvidenceAction(**common, claim_id=claim_id, evidence_id=eid, relation="supports") for eid in evidence_ids)
        return tuple(actions)


__all__ = ["BuiltinRoleActionPlanner", "StructuredLLMRolePolicy"]
