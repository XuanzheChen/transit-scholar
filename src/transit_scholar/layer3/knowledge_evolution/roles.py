from typing import Any
from .models import KnowledgeCandidate

class KnowledgePromotionRole:
    """Predefined semantic promotion component with a bounded input surface."""
    def __init__(self, client: Any | None = None): self.client = client
    def propose(self, normalized: dict[str, Any]) -> list[KnowledgeCandidate]:
        if self.client is not None:
            raw = self.client.generate_structured(normalized)
            return [KnowledgeCandidate.model_validate(x) for x in (raw or [])]
        claims = normalized.get("claims", [])
        if not claims: return []
        evidence_refs = tuple(e["evidence_id"] for e in normalized.get("evidence", []))
        sourced_claims = [claim for claim in claims if claim.get("evidence_ids")]
        if not sourced_claims or not evidence_refs:
            return []
        return [KnowledgeCandidate(candidate_id=f"candidate:{normalized['agent_run_id']}:1", workspace_id=normalized["workspace_id"], originating_agent_run_id=normalized["agent_run_id"], title=str(sourced_claims[0].get("statement", "Knowledge"))[:120], content="\n".join(str(c.get("statement", c)) for c in sourced_claims), source_claim_ids=tuple(c["claim_id"] for c in sourced_claims), evidence_refs=evidence_refs)]
