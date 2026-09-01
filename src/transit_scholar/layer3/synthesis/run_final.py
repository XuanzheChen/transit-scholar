"""Fixed, provenance-preserving run-level final synthesis."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from transit_scholar.layer3.run_context.models import (
    RunContextSnapshot,
    RunFinalResponseArtifact,
    SessionOutcome,
)


class RunFinalSynthesisRole:
    """Synthesize durable outcomes from one or more ResearchSessions.

    This deliberately performs presentation only: it does not create or mutate
    a run-level claim ledger and accepts provenance references already admitted
    by lower layers.
    """

    role_id = "run_final_synthesis"

    def synthesize(
        self,
        snapshot: RunContextSnapshot | Mapping[str, Any],
        *,
        answer_text: str | None = None,
        evidence: Iterable[Any] = (),
        completion_authorized: bool = False,
    ) -> RunFinalResponseArtifact:
        observed = RunContextSnapshot.model_validate(snapshot)
        outcomes = [SessionOutcome.model_validate(item) for item in observed.session_outcomes]
        known_refs: set[str] = set(observed.claim_refs)
        for outcome in outcomes:
            known_refs.update(outcome.claim_refs)
            known_refs.update(outcome.evidence_refs)
            known_refs.update(outcome.source_refs)

        supplied = list(evidence)
        supplied_ids = {self._id(item) for item in supplied}
        unknown = supplied_ids - known_refs if supplied_ids else set()
        if unknown:
            raise ValueError(f"unknown or non-provenance evidence references: {sorted(unknown)}")

        refs = sorted(
            {ref for outcome in outcomes for ref in (*outcome.evidence_refs, *outcome.source_refs)}
            | {ref for ref in supplied_ids if ref}
        )
        if answer_text is None:
            parts = [outcome.final_response or outcome.final_summary for outcome in outcomes]
            parts = [part for part in parts if part]
            answer_text = "\n\n".join(parts) or observed.user_goal
        if not answer_text or not answer_text.strip():
            raise ValueError("answer_text must not be empty")
        failed_outcomes = [outcome for outcome in outcomes if outcome.status != "completed"]
        status = "completed" if completion_authorized else "terminated"
        failure_metadata = {
            "failed_sessions": [
                {
                    "research_session_id": outcome.research_session_id,
                    "status": outcome.status,
                    "failure_reason": outcome.failure_reason,
                }
                for outcome in failed_outcomes
            ]
        }
        return RunFinalResponseArtifact(
            answer_text=answer_text.strip(),
            citation_refs=refs,
            source_refs=refs,
            contributing_session_ids=[o.research_session_id for o in outcomes],
            status=status,
            completion_reason=None,
            failure_metadata=failure_metadata,
        )

    @staticmethod
    def _id(item: Any) -> str | None:
        if isinstance(item, str):
            return item
        if isinstance(item, Mapping):
            return item.get("evidence_id") or item.get("source_id")
        return getattr(item, "evidence_id", None) or getattr(item, "source_id", None)

    def __call__(self, snapshot: RunContextSnapshot | Mapping[str, Any], **kwargs: Any) -> RunFinalResponseArtifact:
        return self.synthesize(snapshot, **kwargs)


__all__ = ["RunFinalSynthesisRole"]
