"""Package A public API for workspace-scoped base wiki snapshots."""

from .models import (
    PageEntityLink,
    PaperMetadata,
    WikiEntity,
    WikiManifest,
    WikiPage,
    WorkspaceContext,
    entity_id_for,
    link_id_for,
    normalize_entity_name,
    page_id_for,
    utc_now,
    validate_identifier,
    AuditIssue,
    IndexRebuildResult,
    PageEntityResult,
    RelatedPageResult,
    UnlinkResult,
    WikiAuditReport,
    WikiSearchHit,
    WikiSearchResult,
)
from .service import (
    WikiAuditError,
    WikiEmbeddingProviderError,
    WikiEmbeddingUnavailableError,
    WikiIndexError,
    WikiMaintainer,
    WikiService,
    WikiServiceError,
    WikiValidationError,
    WikiWorkspaceMismatchError,
)
from .store import (
    WikiConflictError,
    WikiCorruptionError,
    WikiNotFoundError,
    WikiNotInitializedError,
    WikiReferentialIntegrityError,
    WikiStore,
    WikiStoreError,
)
from .field_cards import FieldCard, FieldCardError, FieldCardValidationError, build_field_cards
from .proposals import (
    EntityProposal, EntityProposalRequest, EntityProposalResult, EntityProposalRunner,
    StructuredOutputProvider, build_entity_proposal_request, build_proposal_request,
    build_entity_proposal_prompt, build_proposal_prompt, generate_entity_proposals,
    run_entity_proposals,
)
from .resolution import (
    EntityResolutionCandidate,
    EntityResolutionDecision,
    EntityResolutionResult,
    EntityResolver,
    ResolutionDecisionProvider,
)
from .builder import (
    MAX_PROPOSALS, AuditTrace, BuildPhase, PageTrace, PaperWikiBuildResult,
    ProposalTrace, WorkspaceWikiBuildResult, build_wiki_for_paper,
    build_wiki_for_workspace,
)

__all__ = [
    "PageEntityLink", "PaperMetadata", "WikiEntity", "WikiManifest", "WikiPage",
    "WorkspaceContext", "WikiConflictError", "WikiCorruptionError", "WikiNotFoundError",
    "WikiNotInitializedError", "WikiReferentialIntegrityError", "WikiStore", "WikiStoreError",
    "entity_id_for", "link_id_for", "normalize_entity_name", "page_id_for", "utc_now",
    "validate_identifier",
    "AuditIssue", "IndexRebuildResult", "PageEntityResult", "RelatedPageResult",
    "UnlinkResult", "WikiAuditReport", "WikiSearchHit", "WikiSearchResult",
    "WikiService", "WikiMaintainer", "WikiServiceError", "WikiValidationError",
    "WikiWorkspaceMismatchError", "WikiIndexError", "WikiAuditError",
    "WikiEmbeddingUnavailableError", "WikiEmbeddingProviderError",
    "FieldCard", "FieldCardError", "FieldCardValidationError", "build_field_cards",
    "EntityProposal", "EntityProposalRequest", "EntityProposalResult",
    "EntityProposalRunner", "StructuredOutputProvider", "build_entity_proposal_request",
    "build_proposal_request", "run_entity_proposals",
    "build_entity_proposal_prompt", "build_proposal_prompt", "generate_entity_proposals",
    "EntityResolutionCandidate", "EntityResolutionDecision", "EntityResolutionResult",
    "EntityResolver", "ResolutionDecisionProvider",
    "MAX_PROPOSALS", "BuildPhase", "ProposalTrace", "AuditTrace", "PageTrace",
    "PaperWikiBuildResult", "WorkspaceWikiBuildResult", "build_wiki_for_paper",
    "build_wiki_for_workspace",
]
