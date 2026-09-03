from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import AgenticWikiEntry, KnowledgeCandidate
from .roles import KnowledgePromotionRole
from ..agentic_wiki.store import AgenticWikiStore


@dataclass
class PromotionInput:
    workspace_id: str
    agent_run_id: str
    claims: list[Any]
    evidence: list[Any]
    claim_evidence_links: list[Any] = field(default_factory=list)
    wiki_summaries: list[Any] | None = None
    agent_run_status: str = "completed"


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _identifier(value: Any, primary: str) -> str | None:
    identifier = _get(value, primary, _get(value, "id"))
    return str(identifier) if identifier not in (None, "") else None


def _references(value: Any, *names: str) -> set[str]:
    for name in names:
        references = _get(value, name)
        if references is not None:
            if isinstance(references, str):
                return {references}
            return {str(reference) for reference in references if reference not in (None, "")}
    return set()


class KnowledgePromotionService:
    def __init__(self, role: KnowledgePromotionRole | None = None, store: AgenticWikiStore | None = None):
        self.role = role or KnowledgePromotionRole()
        self.store = store or AgenticWikiStore()
        self._runs: set[tuple[str, str]] = set()

    @property
    def entries(self) -> dict[str, AgenticWikiEntry]:
        return self.store.entries

    def get_entry(self, entry_id: str, workspace_id: str) -> AgenticWikiEntry:
        return self.store.get(entry_id, workspace_id)

    def retrieve(self, workspace_id: str, *, include_stale: bool = False) -> list[dict[str, Any]]:
        return [{"source_kind": "agentic_wiki", "entry": entry} for entry in self.store.list(workspace_id, include_stale=include_stale)]

    def maintain(self, workspace_id: str, **resolvers: Any) -> list[AgenticWikiEntry]:
        return self.store.maintain(workspace_id, **resolvers)

    def collect(self, promotion_input: PromotionInput) -> dict[str, Any]:
        eligible_claims: dict[str, Any] = {}
        for claim in promotion_input.claims:
            claim_id = _identifier(claim, "claim_id")
            if (
                claim_id
                and str(_get(claim, "status", "proposed")) in {"supported", "accepted"}
                and _get(claim, "workspace_id", promotion_input.workspace_id) == promotion_input.workspace_id
                and _get(claim, "agent_run_id", promotion_input.agent_run_id) in (None, promotion_input.agent_run_id)
            ):
                eligible_claims[claim_id] = claim

        evidence_by_id: dict[str, Any] = {}
        evidence_claims: dict[str, set[str]] = {}
        for evidence in promotion_input.evidence:
            evidence_id = _identifier(evidence, "evidence_id")
            if not evidence_id:
                continue
            if _get(evidence, "workspace_id", promotion_input.workspace_id) not in (None, promotion_input.workspace_id):
                continue
            if _get(evidence, "agent_run_id", promotion_input.agent_run_id) not in (None, promotion_input.agent_run_id):
                continue
            if _get(evidence, "status", "admitted") in {"rejected", "ineligible"}:
                continue
            if _get(evidence, "admitted", True) is False:
                continue
            if _get(evidence, "provenance_resolvable", True) is False:
                continue
            if _get(evidence, "source_accessible", _get(evidence, "paper_accessible", True)) is False:
                continue
            evidence_by_id[evidence_id] = evidence
            evidence_claims[evidence_id] = _references(evidence, "claim_ids", "source_claim_ids", "claim_id")

        for claim_id, claim in eligible_claims.items():
            for evidence_id in _references(claim, "evidence_ids", "evidence_refs"):
                evidence_claims.setdefault(evidence_id, set()).add(claim_id)
        for link in promotion_input.claim_evidence_links:
            claim_id = _identifier(link, "claim_id")
            evidence_id = _identifier(link, "evidence_id")
            if claim_id and evidence_id:
                evidence_claims.setdefault(evidence_id, set()).add(claim_id)

        eligible_ids = set(eligible_claims)
        normalized_evidence = []
        for evidence_id, evidence in evidence_by_id.items():
            linked_claim_ids = evidence_claims.get(evidence_id, set()) & eligible_ids
            if linked_claim_ids:
                normalized_evidence.append({
                    "evidence_id": evidence_id,
                    "claim_ids": tuple(sorted(linked_claim_ids)),
                    "provenance": _get(evidence, "provenance", _get(evidence, "source_ref")),
                })

        summaries = [
            {"entry_id": entry.entry_id, "title": entry.title, "content": entry.content, "status": entry.status}
            for entry in self.entries.values()
            if entry.workspace_id == promotion_input.workspace_id and entry.status != "superseded"
        ]
        summaries.extend(
            summary for summary in (promotion_input.wiki_summaries or [])
            if _get(summary, "workspace_id", promotion_input.workspace_id) in (None, promotion_input.workspace_id)
        )
        return {
            "workspace_id": promotion_input.workspace_id,
            "agent_run_id": promotion_input.agent_run_id,
            "claims": [
                {
                    "claim_id": claim_id,
                    "statement": str(_get(claim, "statement", "")),
                    "evidence_ids": tuple(item["evidence_id"] for item in normalized_evidence if claim_id in item["claim_ids"]),
                }
                for claim_id, claim in eligible_claims.items()
            ],
            "evidence": normalized_evidence,
            "wiki_summaries": summaries,
        }

    def run_end(self, promotion_input: PromotionInput) -> list[KnowledgeCandidate]:
        if promotion_input.agent_run_status != "completed":
            raise ValueError("knowledge promotion requires a completed AgentRun")
        run_key = (promotion_input.workspace_id, promotion_input.agent_run_id)
        if run_key in self._runs or self.store.has_promotion_cycle(*run_key):
            return []
        normalized = self.collect(promotion_input)
        candidates = [self._process(candidate, normalized) for candidate in self.role.propose(normalized)]
        self._runs.add(run_key)
        self.store.mark_promotion_cycle(*run_key)
        return candidates

    def _process(self, candidate: KnowledgeCandidate, normalized: dict[str, Any]) -> KnowledgeCandidate:
        try:
            self._validate_candidate(candidate, normalized)
        except (TypeError, ValueError):
            return candidate.model_copy(update={"status": "rejected"})
        accepted = candidate.model_copy(update={"status": "accepted"})
        self._promote(accepted)
        return accepted

    def _validate_candidate(self, candidate: KnowledgeCandidate, normalized: dict[str, Any]) -> None:
        if candidate.status != "proposed":
            raise ValueError("semantic promotion must produce proposed candidates")
        if candidate.workspace_id != normalized["workspace_id"]:
            raise ValueError("candidate belongs to another Workspace")
        if candidate.originating_agent_run_id != normalized["agent_run_id"]:
            raise ValueError("candidate belongs to another AgentRun")
        if not candidate.source_claim_ids or not candidate.evidence_refs:
            raise ValueError("candidate requires Claim and Evidence provenance")

        claims = {claim["claim_id"]: claim for claim in normalized["claims"]}
        evidence = {item["evidence_id"]: item for item in normalized["evidence"]}
        if not set(candidate.source_claim_ids).issubset(claims):
            raise ValueError("candidate references an unknown or ineligible Claim")
        if not set(candidate.evidence_refs).issubset(evidence):
            raise ValueError("candidate references unknown or ineligible Evidence")
        for claim_id in candidate.source_claim_ids:
            if not set(claims[claim_id]["evidence_ids"]).intersection(candidate.evidence_refs):
                raise ValueError("candidate Evidence is inconsistent with its source Claims")
        for evidence_id in candidate.evidence_refs:
            if not set(evidence[evidence_id]["claim_ids"]).intersection(candidate.source_claim_ids):
                raise ValueError("candidate Evidence is unrelated to its source Claims")

        target = candidate.proposed_target_entry_id
        if target:
            entry = self.entries.get(target)
            if entry is None or entry.workspace_id != candidate.workspace_id:
                raise ValueError("candidate update target is unavailable in this Workspace")

    def _promote(self, candidate: KnowledgeCandidate) -> None:
        now = datetime.now(timezone.utc)
        target = candidate.proposed_target_entry_id
        if target:
            entry = self.entries[target]
            self.store.put(entry.model_copy(update={
                "title": candidate.title,
                "content": candidate.content,
                "source_claim_ids": candidate.source_claim_ids,
                "evidence_refs": candidate.evidence_refs,
                "originating_agent_run_id": candidate.originating_agent_run_id,
                "status": "active",
                "updated_at": now,
            }))
            return
        entry_id = f"agentic:{candidate.workspace_id}:{candidate.candidate_id}"
        self.store.put(AgenticWikiEntry(
            entry_id=entry_id,
            workspace_id=candidate.workspace_id,
            title=candidate.title,
            content=candidate.content,
            source_claim_ids=candidate.source_claim_ids,
            evidence_refs=candidate.evidence_refs,
            originating_agent_run_id=candidate.originating_agent_run_id,
        ))
