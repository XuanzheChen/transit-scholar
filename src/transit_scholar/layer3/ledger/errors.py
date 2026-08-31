"""Structural validation errors for the research reasoning ledger."""


class LedgerError(ValueError):
    """Base error for deterministic ledger validation failures."""


class ResearchSessionNotFoundError(LedgerError):
    """The requested ResearchSession does not exist."""


class ResearchQueryNotFoundError(LedgerError):
    """The requested query ledger record does not exist."""


class ResearchQueryOwnershipError(LedgerError):
    """A query does not belong to the requested ResearchSession."""


class InvalidQueryInputError(LedgerError):
    """A query input value is structurally invalid."""


class EvidenceNotFoundError(LedgerError):
    """The requested evidence ledger record does not exist."""


class EvidenceOwnershipError(LedgerError):
    """Evidence does not belong to the requested ResearchSession."""


class InvalidEvidenceInputError(LedgerError):
    """An evidence admission input value is structurally invalid."""


class ClaimNotFoundError(LedgerError):
    """The requested claim ledger record does not exist."""


class ClaimOwnershipError(LedgerError):
    """A claim does not belong to the requested ResearchSession."""


class InvalidClaimInputError(LedgerError):
    """A claim input value is structurally invalid."""


class InvalidClaimEvidenceRelationError(LedgerError):
    """A Claim-Evidence relation is structurally invalid."""


__all__ = [
    "ClaimNotFoundError",
    "ClaimOwnershipError",
    "InvalidQueryInputError",
    "InvalidEvidenceInputError",
    "InvalidClaimEvidenceRelationError",
    "InvalidClaimInputError",
    "EvidenceNotFoundError",
    "EvidenceOwnershipError",
    "LedgerError",
    "ResearchQueryNotFoundError",
    "ResearchQueryOwnershipError",
    "ResearchSessionNotFoundError",
]
