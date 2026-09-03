from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from .models import AgenticWikiEntry, KnowledgeCandidate
from .roles import KnowledgePromotionRole
from ..agentic_wiki.store import AgenticWikiStore


@dataclass
class PromotionInput:
    workspace_id: str
    agent_run_id: str
    claims: list[Any]
    evidence: list[Any]
    claim_evidence_links: list[Any] | None = None
    wiki_summaries: list[Any] | None = None
    agent_run_status: str = "completed"
    research_session_ids: list[str] = field(default_factory=list)


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


def _provenance(value: Any) -> Any:
    for name in ("provenance", "source_ref", "locator", "source_metadata", "paper_id", "source_paper_id"):
        candidate = _get(value, name)
        if candidate not in (None, "", {}, ()):
            return candidate
    return None


def _json_safe(value: Any) -> Any:
    """Convert provider-bound provenance into deterministic JSON values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_json_safe(item) for item in value]
        if isinstance(value, (set, frozenset)):
            values.sort(key=lambda item: repr(item))
        return values
    return value


class KnowledgePromotionService:
    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        *,
        role: KnowledgePromotionRole | None = None,
        base_dir: str | None = None,
        semantic_provider: Any | None = None,
    ) -> "KnowledgePromotionService":
        """Open the durable production promotion service for one Workspace."""
        store = AgenticWikiStore.for_workspace(workspace_id, base_dir=base_dir)
        if role is None:
            role = KnowledgePromotionRole.production(semantic_provider)
        return cls(role=role, store=store, workspace_id=workspace_id)

    def __init__(
        self,
        role: KnowledgePromotionRole | None = None,
        store: AgenticWikiStore | None = None,
        *,
        workspace_id: str | None = None,
        base_dir: str | None = None,
        semantic_provider: Any | None = None,
    ):
        self.role = role or KnowledgePromotionRole.production(semantic_provider)
        if store is None and workspace_id is not None:
            store = AgenticWikiStore.for_workspace(workspace_id, base_dir=base_dir)
        self.store = store or AgenticWikiStore()
        bound_workspace = getattr(self.store, "bound_workspace_id", None)
        if workspace_id is not None and bound_workspace not in (None, workspace_id):
            raise PermissionError("promotion service is bound to another Workspace")
        self.workspace_id = workspace_id or bound_workspace
        self._runs: set[tuple[str, str]] = set()

    @property
    def entries(self) -> Mapping[str, AgenticWikiEntry]:
        entries = self.store.entries
        if self.workspace_id is None:
            return entries
        return MappingProxyType(
            {key: value.model_copy(deep=True) for key, value in entries.items()}
        )

    def get_entry(self, entry_id: str, workspace_id: str) -> AgenticWikiEntry:
        self._check_workspace(workspace_id)
        return self.store.get(entry_id, workspace_id)

    def retrieve(self, workspace_id: str, *, include_stale: bool = False) -> list[dict[str, Any]]:
        self._check_workspace(workspace_id)
        return [{"source_kind": "agentic_wiki", "entry": entry} for entry in self.store.list(workspace_id, include_stale=include_stale)]

    def maintain(self, workspace_id: str, **resolvers: Any) -> list[AgenticWikiEntry]:
        self._check_workspace(workspace_id)
        return self.store.maintain(workspace_id, **resolvers)

    def _check_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if self.workspace_id is not None and workspace_id != self.workspace_id:
            raise PermissionError("promotion service is bound to another Workspace")

    def collect(self, promotion_input: PromotionInput) -> dict[str, Any]:
        self._check_workspace(promotion_input.workspace_id)
        claim_records: dict[str, Any] = {}
        for claim in promotion_input.claims:
            claim_id = _identifier(claim, "claim_id")
            if claim_id:
                claim_records[claim_id] = claim
        eligible_claims: dict[str, Any] = {}
        for claim_id, claim in claim_records.items():
            claim_status = str(_get(claim, "status", "proposed")).casefold()
            if (
                claim_status in {"supported", "accepted"}
                and _get(claim, "workspace_id", promotion_input.workspace_id) == promotion_input.workspace_id
                and _get(claim, "agent_run_id", promotion_input.agent_run_id) in (None, promotion_input.agent_run_id)
                and (
                    not promotion_input.research_session_ids
                    or _get(claim, "research_session_id") in (None, *promotion_input.research_session_ids)
                )
            ):
                eligible_claims[claim_id] = claim

        evidence_records: dict[str, Any] = {}
        for evidence in promotion_input.evidence:
            evidence_id = _identifier(evidence, "evidence_id")
            if evidence_id:
                evidence_records[evidence_id] = evidence
        evidence_by_id: dict[str, Any] = {}
        evidence_claims: dict[str, set[str]] = {}
        for evidence_id, evidence in evidence_records.items():
            if _get(evidence, "workspace_id", promotion_input.workspace_id) not in (None, promotion_input.workspace_id):
                continue
            if _get(evidence, "agent_run_id", promotion_input.agent_run_id) not in (None, promotion_input.agent_run_id):
                continue
            locator = _get(evidence, "locator")
            locator_workspace = _get(locator, "workspace_id") if locator is not None else None
            if locator_workspace not in (None, promotion_input.workspace_id):
                continue
            if (
                promotion_input.research_session_ids
                and _get(evidence, "research_session_id") not in (None, *promotion_input.research_session_ids)
            ):
                continue
            evidence_status = str(_get(evidence, "status", "admitted")).casefold()
            if evidence_status in {"rejected", "ineligible"}:
                continue
            if _get(evidence, "admitted", True) is False:
                continue
            if _get(evidence, "provenance_resolvable", True) is False:
                continue
            if _get(evidence, "source_accessible", _get(evidence, "paper_accessible", True)) is False:
                continue
            evidence_by_id[evidence_id] = evidence
            if promotion_input.claim_evidence_links is None:
                evidence_claims[evidence_id] = _references(
                    evidence, "claim_ids", "source_claim_ids", "claim_id"
                )

        if promotion_input.claim_evidence_links is None:
            for claim_id, claim in eligible_claims.items():
                for evidence_id in _references(claim, "evidence_ids", "evidence_refs"):
                    evidence_claims.setdefault(evidence_id, set()).add(claim_id)
        for link in promotion_input.claim_evidence_links or ():
            claim_id = _identifier(link, "claim_id")
            evidence_id = _identifier(link, "evidence_id")
            relation = str(_get(link, "relation", "supports")).casefold()
            if claim_id and evidence_id and relation == "supports":
                evidence_claims.setdefault(evidence_id, set()).add(claim_id)

        eligible_ids = set(eligible_claims)
        normalized_evidence = []
        for evidence_id, evidence in evidence_by_id.items():
            linked_claim_ids = evidence_claims.get(evidence_id, set()) & eligible_ids
            if linked_claim_ids:
                provenance = _provenance(evidence)
                if provenance in (None, "", {}, ()):
                    continue
                normalized_evidence.append({
                    "evidence_id": evidence_id,
                    "claim_ids": tuple(sorted(linked_claim_ids)),
                    "provenance": _json_safe(provenance),
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
        self._check_workspace(promotion_input.workspace_id)
        run_key = (promotion_input.workspace_id, promotion_input.agent_run_id)
        has_cycle = getattr(self.store, "has_promotion_cycle", None)
        if run_key in self._runs or (has_cycle is not None and has_cycle(*run_key)):
            return []
        normalized = self.collect(promotion_input)
        candidates = []
        accepted_entries = []
        proposed = self.role.propose(normalized) or ()
        for candidate in proposed:
            try:
                if not isinstance(candidate, KnowledgeCandidate):
                    candidate = KnowledgeCandidate.model_validate(candidate)
            except (TypeError, ValueError):
                raise ValueError("semantic promotion returned an invalid candidate")
            processed, entry = self._process(candidate, normalized)
            candidates.append(processed)
            if entry is not None:
                accepted_entries.append(entry)
        if hasattr(self.store, "commit_promotion"):
            self.store.commit_promotion(accepted_entries, *run_key)
        else:
            for entry in accepted_entries:
                self.store.put(entry)
            self.store.mark_promotion_cycle(*run_key)
        self._runs.add(run_key)
        return candidates

    def _process(self, candidate: KnowledgeCandidate, normalized: dict[str, Any]) -> tuple[KnowledgeCandidate, AgenticWikiEntry | None]:
        try:
            candidate = self._resolve_matching_target(candidate)
            self._validate_candidate(candidate, normalized)
        except (TypeError, ValueError):
            return candidate.model_copy(update={"status": "rejected"}), None
        accepted = candidate.model_copy(update={"status": "accepted"})
        return accepted, self._entry_for_candidate(accepted, normalized)

    def _resolve_matching_target(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        if candidate.proposed_target_entry_id:
            return candidate
        finder = getattr(self.store, "find_matching", None)
        if finder is None:
            return candidate
        try:
            matching = finder(candidate.workspace_id, candidate.title, include_stale=True)
        except (PermissionError, ValueError):
            return candidate
        if matching is None or matching.status == "superseded":
            return candidate
        return candidate.model_copy(update={"proposed_target_entry_id": matching.entry_id})

    def _validate_candidate(self, candidate: KnowledgeCandidate, normalized: dict[str, Any]) -> None:
        if candidate.status != "proposed":
            raise ValueError("semantic promotion must produce proposed candidates")
        if candidate.workspace_id != normalized["workspace_id"]:
            raise ValueError("candidate belongs to another Workspace")
        if candidate.originating_agent_run_id != normalized["agent_run_id"]:
            raise ValueError("candidate belongs to another AgentRun")
        evidence_refs = tuple(dict.fromkeys((*candidate.evidence_refs, *candidate.provenance_refs)))
        if not candidate.source_claim_ids or not evidence_refs:
            raise ValueError("candidate requires Claim and Evidence provenance")

        claims = {claim["claim_id"]: claim for claim in normalized["claims"]}
        evidence = {item["evidence_id"]: item for item in normalized["evidence"]}
        if not set(candidate.source_claim_ids).issubset(claims):
            raise ValueError("candidate references an unknown or ineligible Claim")
        if not set(evidence_refs).issubset(evidence):
            raise ValueError("candidate references unknown or ineligible Evidence")
        for claim_id in candidate.source_claim_ids:
            if not set(claims[claim_id]["evidence_ids"]).intersection(evidence_refs):
                raise ValueError("candidate Evidence is inconsistent with its source Claims")
        for evidence_id in evidence_refs:
            if not set(evidence[evidence_id]["claim_ids"]).intersection(candidate.source_claim_ids):
                raise ValueError("candidate Evidence is unrelated to its source Claims")

        target = candidate.proposed_target_entry_id
        if target:
            try:
                entry = self.store.get(target, candidate.workspace_id)
            except (KeyError, PermissionError, ValueError):
                entry = None
            if entry is None or entry.workspace_id != candidate.workspace_id or entry.status == "superseded":
                raise ValueError("candidate update target is unavailable in this Workspace")

    def _entry_for_candidate(
        self, candidate: KnowledgeCandidate, normalized: dict[str, Any]
    ) -> AgenticWikiEntry:
        now = datetime.now(timezone.utc)
        target = candidate.proposed_target_entry_id
        evidence_refs = tuple(dict.fromkeys((*candidate.evidence_refs, *candidate.provenance_refs)))
        paper_ids = self._paper_ids_for_candidate(evidence_refs, normalized)
        if target:
            entry = self.store.get(target, candidate.workspace_id)
            return entry.model_copy(update={
                "title": candidate.title,
                "content": candidate.content,
                "source_claim_ids": candidate.source_claim_ids,
                "evidence_refs": evidence_refs,
                "provenance_refs": candidate.provenance_refs or evidence_refs,
                "paper_ids": paper_ids or entry.paper_ids,
                "originating_agent_run_id": candidate.originating_agent_run_id,
                "status": "active",
                "updated_at": now,
            })
        entry_id = f"agentic:{candidate.workspace_id}:{candidate.candidate_id}"
        return AgenticWikiEntry(
            entry_id=entry_id,
            workspace_id=candidate.workspace_id,
            title=candidate.title,
            content=candidate.content,
            source_claim_ids=candidate.source_claim_ids,
            evidence_refs=evidence_refs,
            provenance_refs=candidate.provenance_refs or evidence_refs,
            paper_ids=paper_ids,
            originating_agent_run_id=candidate.originating_agent_run_id,
        )

    @staticmethod
    def _paper_ids_for_candidate(
        evidence_refs: tuple[str, ...], normalized: dict[str, Any]
    ) -> tuple[str, ...]:
        paper_ids: set[str] = set()
        for evidence in normalized.get("evidence", ()):
            if evidence.get("evidence_id") not in evidence_refs:
                continue
            provenance = evidence.get("provenance")
            if isinstance(provenance, Mapping):
                paper_id = provenance.get("paper_id") or provenance.get("source_paper_id")
                if paper_id:
                    paper_ids.add(str(paper_id))
        return tuple(sorted(paper_ids))
