"""Deterministic collection, semantic distillation, and validation for episodes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import EpisodicMemoryProvenance, EpisodicMemoryRecord


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _values(value: Any, *, identity_fields: tuple[str, ...] = ()) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        if any(field in value for field in identity_fields):
            return [value]
        return list(value.values())
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _status(value: Any, default: str) -> str:
    if isinstance(value, dict):
        raw = value.get("status", default)
    else:
        raw = getattr(value, "status", default)
    raw = _get(raw, "value", raw)
    return str(raw if raw is not None else default).casefold()


class NormalizedEpisodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    user_goal_raw: str = Field(min_length=1)
    session_ids: tuple[str, ...] = ()
    useful_queries: tuple[str, ...] = ()
    failed_or_unhelpful_queries: tuple[str, ...] = ()
    final_outcome: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    durable_state_refs: tuple[str, ...] = ()


class EpisodicSemanticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_summary: str = Field(min_length=1)
    research_summary: str = Field(min_length=1)
    important_claim_ids: tuple[str, ...] = ()
    unresolved_summary: str = ""


class EpisodicMemoryCollector:
    def collect(self, agent_run: Any, *, workspace_id: str | None = None, queries: Any = None,
                evidence: Any = None, claims: Any = None, final_outcome: str | None = None,
                user_goal_raw: str | None = None) -> NormalizedEpisodeInput:
        run_workspace = _get(agent_run, "workspace_id")
        if (
            workspace_id is not None
            and run_workspace is not None
            and workspace_id != run_workspace
        ):
            raise PermissionError("AgentRun belongs to another Workspace")
        ws = workspace_id or run_workspace
        run_id = _get(agent_run, "agent_run_id", _get(agent_run, "id"))
        run_goal = _get(agent_run, "user_goal_raw", _get(agent_run, "user_goal"))
        if user_goal_raw is not None and run_goal is not None and user_goal_raw != run_goal:
            raise ValueError("user_goal_raw does not match the authoritative AgentRun goal")
        goal = user_goal_raw if user_goal_raw is not None else run_goal
        sessions = _get(agent_run, "sessions", _get(agent_run, "research_sessions", ())) or ()
        for session in sessions:
            session_workspace = _get(session, "workspace_id")
            if session_workspace is not None and session_workspace != ws:
                raise PermissionError("ResearchSession belongs to another Workspace")
            session_run = _get(session, "agent_run_id")
            if session_run is not None and session_run != run_id:
                raise ValueError("ResearchSession belongs to another AgentRun")
        session_ids = tuple(
            _get(s, "research_session_id", _get(s, "session_id", _get(s, "id")))
            for s in sessions
        )
        qs = _values(queries if queries is not None else (_get(agent_run, "queries", ()) or ()), identity_fields=("query_id", "id"))
        qs = [
            query for query in qs
            if _get(query, "research_session_id") in (None, *session_ids)
            and _get(query, "workspace_id", ws) in (None, ws)
            and _get(query, "agent_run_id", run_id) in (None, run_id)
        ]
        evs = _values(evidence if evidence is not None else (_get(agent_run, "evidence", ()) or ()), identity_fields=("evidence_id", "id"))
        eligible_evidence = []
        for item in evs:
            item_workspace = _get(item, "workspace_id")
            locator = _get(item, "locator")
            if item_workspace is None and locator is not None:
                item_workspace = _get(locator, "workspace_id")
            item_run = _get(item, "agent_run_id")
            item_session = _get(item, "research_session_id")
            if item_workspace not in (None, ws):
                continue
            if item_run not in (None, run_id):
                continue
            if session_ids and item_session not in (None, *session_ids):
                continue
            eligible_evidence.append(item)
        evs = eligible_evidence
        admitted = {
            str(_get(e, "source_query_id", _get(e, "query_id")))
            for e in evs
            if _get(e, "admitted", True)
            and _status(e, "admitted")
            in ("admitted", "accepted", "active")
            and _get(e, "source_query_id", _get(e, "query_id")) is not None
        }
        useful, failed, seen_query_ids = [], [], set()
        for q in qs:
            raw_qid = _get(q, "query_id", _get(q, "id"))
            if raw_qid is None:
                continue
            qid, text = str(raw_qid), str(_get(q, "query_text", _get(q, "text", q)))
            if qid in seen_query_ids:
                continue
            seen_query_ids.add(qid)
            status = _status(q, "completed")
            terminal_success = status in {"completed", "succeeded", "success"}
            (useful if qid in admitted and terminal_success else failed).append(text)
        cls = _values(claims if claims is not None else (_get(agent_run, "claims", ()) or ()), identity_fields=("claim_id", "id"))
        cls = [
            claim for claim in cls
            if _get(claim, "workspace_id", ws) == ws
            and _get(claim, "agent_run_id", run_id) in (None, run_id)
            and (
                not session_ids
                or _get(claim, "research_session_id") in (None, *session_ids)
            )
        ]
        claim_ids = tuple(
            str(claim_id)
            for c in cls
            if (claim_id := _get(c, "claim_id", _get(c, "id"))) is not None
        )
        evidence_ids = tuple(
            str(evidence_id)
            for e in evs
            if (evidence_id := _get(e, "evidence_id", _get(e, "id"))) is not None
        )
        outcome = final_outcome or str(_get(agent_run, "final_outcome", _get(agent_run, "outcome", "completed")))
        return NormalizedEpisodeInput(workspace_id=ws, agent_run_id=run_id, user_goal_raw=goal,
            session_ids=tuple(x for x in session_ids if x), useful_queries=tuple(useful),
            failed_or_unhelpful_queries=tuple(failed), final_outcome=outcome,
            claim_ids=claim_ids, evidence_ids=evidence_ids,
            durable_state_refs=(f"agent-run:{run_id}",))


class EpisodicMemoryDistiller:
    def __init__(self, client: Any | None = None, *, require_semantic_provider: bool = False, degraded_fallback: bool = True):
        self.client = client
        self.require_semantic_provider = require_semantic_provider
        self.degraded_fallback = degraded_fallback

    @classmethod
    def production(cls, client: Any | None = None) -> "EpisodicMemoryDistiller":
        return cls(client, require_semantic_provider=True, degraded_fallback=False)

    def distill(self, normalized: NormalizedEpisodeInput) -> EpisodicSemanticOutput:
        if self.client is None:
            if self.require_semantic_provider and not self.degraded_fallback:
                raise RuntimeError("semantic distillation requires an explicit semantic provider")
            return EpisodicSemanticOutput(goal_summary=normalized.user_goal_raw, research_summary=normalized.final_outcome)
        bounded_input = normalized.model_dump(mode="json")
        messages = [
            {"role": "user", "content": json.dumps(bounded_input, sort_keys=True)}
        ]
        metadata = {
            "workspace_id": normalized.workspace_id,
            "agent_run_id": normalized.agent_run_id,
            "normalized_episode_input": bounded_input,
        }
        generator = getattr(self.client, "generate_structured", None)
        if callable(generator):
            raw = generator(messages, EpisodicSemanticOutput, metadata)
        elif callable(self.client):
            try:
                raw = self.client(bounded_input, EpisodicSemanticOutput, metadata)
            except TypeError:
                raw = self.client(bounded_input)
        else:
            raise TypeError(
                "semantic provider must expose generate_structured or be callable"
            )
        return EpisodicSemanticOutput.model_validate(raw)


def validate_semantic_output(output: EpisodicSemanticOutput, normalized: NormalizedEpisodeInput,
                             claims: Any = None) -> EpisodicSemanticOutput:
    allowed = set(normalized.claim_ids)
    selected = set(output.important_claim_ids)
    if not selected.issubset(allowed):
        raise ValueError("unknown or unauthorized claim reference")
    if claims is not None:
        claims_by_id = {
            str(_get(claim, "claim_id", _get(claim, "id"))): claim
            for claim in _values(claims, identity_fields=("claim_id", "id"))
            if _get(claim, "claim_id", _get(claim, "id")) is not None
        }
        if not selected.issubset(claims_by_id):
            raise ValueError("referenced claim does not exist")
        for claim_id in selected:
            claim = claims_by_id[claim_id]
            if _get(claim, "workspace_id", normalized.workspace_id) != normalized.workspace_id:
                raise ValueError("claim ownership mismatch")
            claim_run_id = _get(claim, "agent_run_id")
            if claim_run_id is not None and claim_run_id != normalized.agent_run_id:
                raise ValueError("claim ownership mismatch")
            session_id = _get(claim, "research_session_id")
            if claim_run_id is None and session_id is None:
                raise ValueError("claim ownership mismatch")
            if session_id is not None and session_id not in normalized.session_ids:
                raise ValueError("claim ownership mismatch")
    return output


def build_episodic_record(
    normalized: NormalizedEpisodeInput,
    semantic: EpisodicSemanticOutput,
    *,
    claims: Any = None,
    created_at: datetime | None = None,
) -> EpisodicMemoryRecord:
    validate_semantic_output(semantic, normalized, claims=claims)
    return EpisodicMemoryRecord(memory_id=EpisodicMemoryRecord.canonical_memory_id(normalized.agent_run_id),
        workspace_id=normalized.workspace_id, agent_run_id=normalized.agent_run_id, user_goal_raw=normalized.user_goal_raw,
        goal_summary=semantic.goal_summary, research_summary=semantic.research_summary,
        important_claim_ids=semantic.important_claim_ids, useful_queries=normalized.useful_queries,
        failed_or_unhelpful_queries=normalized.failed_or_unhelpful_queries, unresolved_summary=semantic.unresolved_summary,
        final_outcome=normalized.final_outcome, provenance=EpisodicMemoryProvenance(workspace_id=normalized.workspace_id,
        agent_run_id=normalized.agent_run_id, research_session_ids=normalized.session_ids, claim_ids=normalized.claim_ids,
        evidence_ids=normalized.evidence_ids, durable_state_refs=normalized.durable_state_refs), created_at=created_at or datetime.now(timezone.utc))


class EpisodicMemoryEvidenceError(ValueError): pass
def ensure_auxiliary_memory(record: EpisodicMemoryRecord) -> None:
    if record.is_authoritative_evidence: raise EpisodicMemoryEvidenceError("episodic memory is not ResearchEvidence")

__all__ = ["NormalizedEpisodeInput", "EpisodicSemanticOutput", "EpisodicMemoryCollector", "EpisodicMemoryDistiller", "validate_semantic_output", "build_episodic_record", "ensure_auxiliary_memory", "EpisodicMemoryEvidenceError"]
