from typing import Any
from .models import KnowledgeCandidate

class KnowledgePromotionRole:
    """Predefined semantic promotion component with a bounded input surface."""
    def __init__(self, client: Any | None = None, *, require_semantic_provider: bool = False, degraded_fallback: bool = True):
        self.client = client
        self.require_semantic_provider = require_semantic_provider
        self.degraded_fallback = degraded_fallback

    @classmethod
    def production(cls, client: Any | None = None) -> "KnowledgePromotionRole":
        return cls(client, require_semantic_provider=True, degraded_fallback=False)

    def propose(self, normalized: dict[str, Any]) -> list[KnowledgeCandidate]:
        if self.client is not None:
            raw = self.client.generate_structured(normalized)
            return [KnowledgeCandidate.model_validate(x) for x in (raw or [])]
        if self.require_semantic_provider and not self.degraded_fallback:
            raise RuntimeError("semantic promotion requires an explicit semantic provider")
        claims = normalized.get("claims", [])
        if not claims: return []
        evidence_refs = tuple(e["evidence_id"] for e in normalized.get("evidence", []))
        sourced_claims = [claim for claim in claims if claim.get("evidence_ids")]
        if not sourced_claims or not evidence_refs:
            return []
        return [KnowledgeCandidate(candidate_id=f"candidate:{normalized['agent_run_id']}:1", workspace_id=normalized["workspace_id"], originating_agent_run_id=normalized["agent_run_id"], title=str(sourced_claims[0].get("statement", "Knowledge"))[:120], content="\n".join(str(c.get("statement", c)) for c in sourced_claims), source_claim_ids=tuple(c["claim_id"] for c in sourced_claims), evidence_refs=evidence_refs)]
