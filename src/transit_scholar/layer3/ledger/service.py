"""Framework-neutral persistence APIs for the Research Query Ledger."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from transit_scholar.db.models import (
    CLAIM_EVIDENCE_RELATIONS,
    CLAIM_STATUSES,
    RESEARCH_QUERY_STATUSES,
    ClaimEvidenceLink as ClaimEvidenceLinkRow,
    ClaimRecord as ClaimRow,
    EvidenceRecord as EvidenceRow,
    ResearchQueryRecord as ResearchQueryRow,
    ResearchSession,
)
from transit_scholar.layer3.evidence import ResearchEvidence

from .errors import (
    ClaimNotFoundError,
    ClaimOwnershipError,
    EvidenceNotFoundError,
    EvidenceOwnershipError,
    InvalidEvidenceInputError,
    InvalidClaimEvidenceRelationError,
    InvalidClaimInputError,
    InvalidQueryInputError,
    ResearchQueryNotFoundError,
    ResearchQueryOwnershipError,
    ResearchSessionNotFoundError,
)
from .models import ClaimEvidenceLink, ClaimRecord, EvidenceRecord, ResearchQueryRecord


def _new_id() -> str:
    return uuid.uuid4().hex


def _non_empty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidQueryInputError(f"{label} must be a non-empty string")
    return value.strip()


def _status(value: str) -> str:
    value = _non_empty(value, "status")
    if value not in RESEARCH_QUERY_STATUSES:
        raise InvalidQueryInputError(
            f"status must be one of {RESEARCH_QUERY_STATUSES!r}"
        )
    return value


def _claim_value(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidClaimInputError(f"{label} must be a non-empty string")
    return value.strip()


def _claim_status(value: str) -> str:
    value = _claim_value(value, "status")
    if value not in CLAIM_STATUSES:
        raise InvalidClaimInputError(f"status must be one of {CLAIM_STATUSES!r}")
    return value


def _claim_relation(value: str) -> str:
    if not isinstance(value, str) or value not in CLAIM_EVIDENCE_RELATIONS:
        raise InvalidClaimEvidenceRelationError(
            f"relation must be one of {CLAIM_EVIDENCE_RELATIONS!r}"
        )
    return value


class ResearchQueryLedgerService:
    """Persist caller-created queries; this service never generates queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_query(
        self,
        *,
        research_session_id: str,
        query_text: str,
        status: str = "active",
        parent_query_id: str | None = None,
        query_id: str | None = None,
    ) -> ResearchQueryRecord:
        research_session = self._get_session(research_session_id)
        if parent_query_id is not None:
            self._get_owned_query(research_session.id, parent_query_id)
        row = ResearchQueryRow(
            id=_non_empty(query_id, "query_id") if query_id else _new_id(),
            research_session_id=research_session.id,
            query_text=_non_empty(query_text, "query_text"),
            status=_status(status),
            parent_query_id=parent_query_id,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return ResearchQueryRecord.from_row(row)

    def get_query(
        self, *, research_session_id: str, query_id: str
    ) -> ResearchQueryRecord:
        self._get_session(research_session_id)
        return ResearchQueryRecord.from_row(
            self._get_owned_query(research_session_id, query_id)
        )

    read_query = get_query

    def list_queries(self, *, research_session_id: str) -> list[ResearchQueryRecord]:
        session = self._get_session(research_session_id)
        rows = self.session.execute(
            select(ResearchQueryRow)
            .where(ResearchQueryRow.research_session_id == session.id)
            .order_by(ResearchQueryRow.created_at, ResearchQueryRow.id)
        ).scalars().all()
        return [ResearchQueryRecord.from_row(row) for row in rows]

    def update_query_status(
        self, *, research_session_id: str, query_id: str, status: str
    ) -> ResearchQueryRecord:
        self._get_session(research_session_id)
        row = self._get_owned_query(research_session_id, query_id)
        row.status = _status(status)
        self.session.flush()
        self.session.refresh(row)
        return ResearchQueryRecord.from_row(row)

    def admit_evidence(
        self,
        *,
        research_session_id: str,
        source_query_id: str,
        evidence: ResearchEvidence,
    ) -> EvidenceRecord:
        """Persist one caller-selected retrieval result as a stable snapshot."""
        research_session = self._get_session(research_session_id)
        self._get_owned_query(research_session.id, source_query_id)
        if not isinstance(evidence, ResearchEvidence):
            raise InvalidEvidenceInputError("evidence must be a ResearchEvidence")
        if evidence.paper_provenance is not None and (
            evidence.paper_provenance.parse_run_id or evidence.paper_provenance.canonical_source_version
        ):
            locator_identity = (
                evidence.locator.canonical_source_version
                or evidence.locator.parse_run_id
            )
            provenance_identity = (
                evidence.paper_provenance.canonical_source_version
                or evidence.paper_provenance.parse_run_id
            )
            if locator_identity and provenance_identity and locator_identity != provenance_identity:
                raise InvalidEvidenceInputError(
                    "Paper evidence locator and paper provenance source identities conflict"
                )
            updates = {}
            if evidence.locator.parse_run_id is None:
                updates["parse_run_id"] = evidence.paper_provenance.parse_run_id
            if evidence.locator.canonical_source_version is None:
                updates["canonical_source_version"] = evidence.paper_provenance.canonical_source_version
            if updates:
                evidence = evidence.model_copy(update={"locator": evidence.locator.model_copy(update=updates)})
        self._validate_evidence_provenance(
            research_session=research_session,
            source_query_id=source_query_id,
            evidence=evidence,
        )
        try:
            source_metadata = {
                "source_kind": evidence.source_kind,
                "paper_provenance": (
                    evidence.paper_provenance.model_dump(mode="json")
                    if evidence.paper_provenance is not None else None
                ),
                "section": evidence.section,
            }
            retrieval_provenance = {
                "retrieval_evidence_id": evidence.evidence_id,
                "query_provenance": (
                    evidence.query_provenance.model_dump(mode="json")
                    if evidence.query_provenance is not None else None
                ),
                "retrieval_provenance": evidence.retrieval_provenance,
                "rerank_provenance": evidence.rerank_provenance,
                "final_rank": evidence.final_rank,
            }
            row = EvidenceRow(
                id=_new_id(),
                research_session_id=research_session.id,
                source_query_id=source_query_id,
                locator_json=json.dumps(
                    evidence.locator.model_dump(mode="json"), sort_keys=True
                ),
                text_snapshot=evidence.text,
                source_metadata_json=json.dumps(source_metadata, sort_keys=True),
                retrieval_provenance_json=json.dumps(
                    retrieval_provenance, sort_keys=True
                ),
            )
        except (TypeError, ValueError) as exc:
            raise InvalidEvidenceInputError(
                "evidence provenance must be JSON-serializable"
            ) from exc
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return EvidenceRecord.from_row(row)

    @staticmethod
    def _validate_evidence_provenance(
        *,
        research_session: ResearchSession,
        source_query_id: str,
        evidence: ResearchEvidence,
    ) -> None:
        query_provenance = evidence.query_provenance
        if evidence.locator.source_kind.casefold() == "paper":
            locator_identity = (
                evidence.locator.canonical_source_version
                or evidence.locator.parse_run_id
            )
            provenance_identity = (
                (
                    evidence.paper_provenance.canonical_source_version
                    or evidence.paper_provenance.parse_run_id
                )
                if evidence.paper_provenance is not None
                else None
            )
            if locator_identity is None and provenance_identity is None:
                raise InvalidEvidenceInputError(
                    "Paper evidence requires a stable source identity"
                )
            if (
                locator_identity is not None
                and provenance_identity is not None
                and locator_identity != provenance_identity
            ):
                raise InvalidEvidenceInputError(
                    "Paper evidence locator and paper provenance source identities conflict"
                )
        if (
            query_provenance is not None
            and query_provenance.query_id != source_query_id
        ):
            raise InvalidEvidenceInputError(
                "evidence query provenance does not match source_query_id"
            )
        if (
            query_provenance is not None
            and query_provenance.session_id is not None
            and query_provenance.session_id != research_session.id
        ):
            raise InvalidEvidenceInputError(
                "evidence session provenance does not match research_session_id"
            )
        if evidence.locator.workspace_id != research_session.agent_run.workspace_id:
            raise InvalidEvidenceInputError(
                "evidence workspace provenance does not match the research session workspace"
            )
        if (
            evidence.paper_provenance is not None
            and evidence.locator.paper_id is not None
            and evidence.paper_provenance.paper_id != evidence.locator.paper_id
        ):
            raise InvalidEvidenceInputError(
                "evidence paper provenance does not match locator.paper_id"
            )

    def get_evidence(
        self, *, research_session_id: str, evidence_id: str
    ) -> EvidenceRecord:
        self._get_session(research_session_id)
        return EvidenceRecord.from_row(
            self._get_owned_evidence(research_session_id, evidence_id)
        )

    read_evidence = get_evidence

    def list_evidence(self, *, research_session_id: str) -> list[EvidenceRecord]:
        research_session = self._get_session(research_session_id)
        rows = self.session.execute(
            select(EvidenceRow)
            .where(EvidenceRow.research_session_id == research_session.id)
            .order_by(EvidenceRow.created_at, EvidenceRow.id)
        ).scalars().all()
        return [EvidenceRecord.from_row(row) for row in rows]

    def create_claim(
        self,
        *,
        research_session_id: str,
        statement: str,
        status: str = "proposed",
        rationale: str | None = None,
        claim_id: str | None = None,
    ) -> ClaimRecord:
        research_session = self._get_session(research_session_id)
        row = ClaimRow(
            id=_claim_value(claim_id, "claim_id") if claim_id else _new_id(),
            research_session_id=research_session.id,
            statement=_claim_value(statement, "statement"),
            status=_claim_status(status),
            rationale=_claim_value(rationale, "rationale", optional=True),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return ClaimRecord.from_row(row)

    def get_claim(self, *, research_session_id: str, claim_id: str) -> ClaimRecord:
        self._get_session(research_session_id)
        return ClaimRecord.from_row(self._get_owned_claim(research_session_id, claim_id))

    read_claim = get_claim

    def list_claims(self, *, research_session_id: str) -> list[ClaimRecord]:
        research_session = self._get_session(research_session_id)
        rows = self.session.execute(
            select(ClaimRow)
            .where(ClaimRow.research_session_id == research_session.id)
            .order_by(ClaimRow.created_at, ClaimRow.id)
        ).scalars().all()
        return [ClaimRecord.from_row(row) for row in rows]

    def update_claim(
        self,
        *,
        research_session_id: str,
        claim_id: str,
        status: str | None = None,
        rationale: str | None = None,
    ) -> ClaimRecord:
        self._get_session(research_session_id)
        row = self._get_owned_claim(research_session_id, claim_id)
        if status is None and rationale is None:
            raise InvalidClaimInputError("status or rationale must be provided")
        if status is not None:
            row.status = _claim_status(status)
        if rationale is not None:
            row.rationale = _claim_value(rationale, "rationale")
        self.session.flush()
        self.session.refresh(row)
        return ClaimRecord.from_row(row)

    def update_claim_status(
        self, *, research_session_id: str, claim_id: str, status: str
    ) -> ClaimRecord:
        return self.update_claim(
            research_session_id=research_session_id, claim_id=claim_id, status=status
        )

    def link_evidence_to_claim(
        self,
        *,
        research_session_id: str,
        claim_id: str,
        evidence_id: str,
        relation: str,
    ) -> ClaimEvidenceLink:
        self._get_session(research_session_id)
        claim = self._get_owned_claim(research_session_id, claim_id)
        evidence = self._get_owned_evidence(research_session_id, evidence_id)
        existing = self.session.execute(
            select(ClaimEvidenceLinkRow).where(
                ClaimEvidenceLinkRow.claim_id == claim.id,
                ClaimEvidenceLinkRow.evidence_id == evidence.id,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ClaimEvidenceLinkRow(
                claim_id=claim.id, evidence_id=evidence.id, relation=_claim_relation(relation)
            )
            self.session.add(existing)
        else:
            existing.relation = _claim_relation(relation)
        self.session.flush()
        self.session.refresh(existing)
        return ClaimEvidenceLink.from_row(existing)

    def unlink_evidence_from_claim(
        self, *, research_session_id: str, claim_id: str, evidence_id: str
    ) -> None:
        self._get_session(research_session_id)
        claim = self._get_owned_claim(research_session_id, claim_id)
        evidence = self._get_owned_evidence(research_session_id, evidence_id)
        row = self.session.execute(
            select(ClaimEvidenceLinkRow).where(
                ClaimEvidenceLinkRow.claim_id == claim.id,
                ClaimEvidenceLinkRow.evidence_id == evidence.id,
            )
        ).scalar_one_or_none()
        if row is not None:
            self.session.delete(row)
            self.session.flush()

    def get_claim_evidence(
        self, *, research_session_id: str, claim_id: str
    ) -> list[ClaimEvidenceLink]:
        self._get_session(research_session_id)
        claim = self._get_owned_claim(research_session_id, claim_id)
        rows = self.session.execute(
            select(ClaimEvidenceLinkRow)
            .where(ClaimEvidenceLinkRow.claim_id == claim.id)
            .order_by(ClaimEvidenceLinkRow.created_at, ClaimEvidenceLinkRow.id)
        ).scalars().all()
        return [ClaimEvidenceLink.from_row(row) for row in rows]

    def _get_session(self, research_session_id: str) -> ResearchSession:
        research_session_id = _non_empty(research_session_id, "research_session_id")
        row = self.session.get(ResearchSession, research_session_id)
        if row is None:
            raise ResearchSessionNotFoundError(
                f"research session {research_session_id!r} does not exist"
            )
        return row

    def _get_owned_query(
        self, research_session_id: str, query_id: str
    ) -> ResearchQueryRow:
        query_id = _non_empty(query_id, "query_id")
        row = self.session.get(ResearchQueryRow, query_id)
        if row is None:
            raise ResearchQueryNotFoundError(f"query {query_id!r} does not exist")
        if row.research_session_id != research_session_id:
            raise ResearchQueryOwnershipError(
                f"query {query_id!r} is not owned by research session {research_session_id!r}"
            )
        return row

    def _get_owned_evidence(
        self, research_session_id: str, evidence_id: str
    ) -> EvidenceRow:
        evidence_id = _non_empty(evidence_id, "evidence_id")
        row = self.session.get(EvidenceRow, evidence_id)
        if row is None:
            raise EvidenceNotFoundError(f"evidence {evidence_id!r} does not exist")
        if row.research_session_id != research_session_id:
            raise EvidenceOwnershipError(
                f"evidence {evidence_id!r} is not owned by research session {research_session_id!r}"
            )
        return row

    def _get_owned_claim(self, research_session_id: str, claim_id: str) -> ClaimRow:
        claim_id = _claim_value(claim_id, "claim_id")
        row = self.session.get(ClaimRow, claim_id)
        if row is None:
            raise ClaimNotFoundError(f"claim {claim_id!r} does not exist")
        if row.research_session_id != research_session_id:
            raise ClaimOwnershipError(
                f"claim {claim_id!r} is not owned by research session {research_session_id!r}"
            )
        return row


QueryLedgerService = ResearchQueryLedgerService
ResearchReasoningLedgerService = ResearchQueryLedgerService


class QueryService:
    """Framework-neutral public operations for persisted research queries."""

    def __init__(self, session: Session) -> None:
        self._ledger = ResearchReasoningLedgerService(session)

    def create_query(self, **kwargs: Any) -> ResearchQueryRecord:
        return self._ledger.create_query(**kwargs)

    def get_query(self, **kwargs: Any) -> ResearchQueryRecord:
        return self._ledger.get_query(**kwargs)

    read_query = get_query

    def list_queries(self, **kwargs: Any) -> list[ResearchQueryRecord]:
        return self._ledger.list_queries(**kwargs)

    def update_query_status(self, **kwargs: Any) -> ResearchQueryRecord:
        return self._ledger.update_query_status(**kwargs)


class EvidenceService:
    """Framework-neutral public operations for explicitly admitted evidence."""

    def __init__(self, session: Session) -> None:
        self._ledger = ResearchReasoningLedgerService(session)

    def admit_evidence(self, **kwargs: Any) -> EvidenceRecord:
        return self._ledger.admit_evidence(**kwargs)

    def get_evidence(self, **kwargs: Any) -> EvidenceRecord:
        return self._ledger.get_evidence(**kwargs)

    read_evidence = get_evidence

    def list_evidence(self, **kwargs: Any) -> list[EvidenceRecord]:
        return self._ledger.list_evidence(**kwargs)


class ClaimService:
    """Framework-neutral public operations for caller-created claims."""

    def __init__(self, session: Session) -> None:
        self._ledger = ResearchReasoningLedgerService(session)

    def create_claim(self, **kwargs: Any) -> ClaimRecord:
        return self._ledger.create_claim(**kwargs)

    def get_claim(self, **kwargs: Any) -> ClaimRecord:
        return self._ledger.get_claim(**kwargs)

    read_claim = get_claim

    def list_claims(self, **kwargs: Any) -> list[ClaimRecord]:
        return self._ledger.list_claims(**kwargs)

    def update_claim(self, **kwargs: Any) -> ClaimRecord:
        return self._ledger.update_claim(**kwargs)

    def update_claim_status(self, **kwargs: Any) -> ClaimRecord:
        return self._ledger.update_claim_status(**kwargs)


class ClaimEvidenceLinkService:
    """Framework-neutral public operations for Claim-Evidence relationships."""

    def __init__(self, session: Session) -> None:
        self._ledger = ResearchReasoningLedgerService(session)

    def link_evidence_to_claim(self, **kwargs: Any) -> ClaimEvidenceLink:
        return self._ledger.link_evidence_to_claim(**kwargs)

    def unlink_evidence_from_claim(self, **kwargs: Any) -> None:
        self._ledger.unlink_evidence_from_claim(**kwargs)

    def get_claim_evidence(self, **kwargs: Any) -> list[ClaimEvidenceLink]:
        return self._ledger.get_claim_evidence(**kwargs)

__all__ = [
    "ClaimEvidenceLinkService",
    "ClaimService",
    "EvidenceService",
    "QueryLedgerService",
    "QueryService",
    "ResearchQueryLedgerService",
    "ResearchReasoningLedgerService",
]
