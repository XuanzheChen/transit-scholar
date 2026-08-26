"""Application-level composition for authoritative Workspace Wiki builds."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from transit_scholar.layer2.schema_extraction.api import get_schema
from transit_scholar.layer2.schema_extraction.loader import get_schema_definition
from transit_scholar.layer2.schema_extraction.models import SchemaDefinition, SchemaInstance
from transit_scholar.metadata.service import read_paper_metadata

from .builder import WorkspaceWikiBuildResult, build_wiki_for_workspace
from .models import IndexRebuildResult, PaperMetadata, WikiAuditReport, WikiManifest, WorkspaceContext
from .providers import WikiProductionComposition, create_production_wiki_composition
from .service import WikiIndexError
from .store import WikiStore

WIKI_BUILDER_VERSION = "wiki-core-v1"


class WikiBuildInputError(ValueError):
    """An authoritative build input is absent or incompatible with its workspace."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkspaceWikiBuildInputs(BaseModel):
    """Validated authoritative assets used by one Workspace Wiki build."""

    model_config = ConfigDict(frozen=True)
    context: WorkspaceContext
    definition: SchemaDefinition
    instances_by_paper: dict[str, SchemaInstance]
    metadata_by_paper: dict[str, PaperMetadata]


class WorkspaceWikiApplicationBuildResult(BaseModel):
    """The build result plus mandatory persisted-artifact finalization evidence."""

    model_config = ConfigDict(frozen=True)
    build: WorkspaceWikiBuildResult
    manifest: WikiManifest
    index: IndexRebuildResult
    audit: WikiAuditReport


SchemaDefinitionLoader = Callable[[str], SchemaDefinition]
SchemaInstanceLoader = Callable[[str, str], SchemaInstance]
PaperMetadataLoader = Callable[[str], PaperMetadata | None]
StoreFactory = Callable[[WorkspaceContext], WikiStore]
CompositionFactory = Callable[[WorkspaceContext, WikiStore], WikiProductionComposition]


def _manifest_build_status(build: WorkspaceWikiBuildResult) -> str:
    """Map a workspace result to a safe persisted manifest status."""
    if build.status == "failed":
        return "failed"
    if build.status == "complete":
        papers_complete = (
            bool(build.papers)
            and build.complete_count == len(build.papers)
            and build.incomplete_count == 0
            and build.failed_count == 0
            and all(paper.status == "complete" for paper in build.papers)
        )
        return "complete" if papers_complete else "partial"
    return "partial"


def _load_current_schema_instance(paper_id: str, schema_id: str) -> SchemaInstance:
    return get_schema(paper_id, schema_id)


class WorkspaceWikiBuildService:
    """Load, validate, compose, and finalize one Workspace Wiki build."""

    def __init__(
        self,
        *,
        schema_definition_loader: SchemaDefinitionLoader = get_schema_definition,
        schema_instance_loader: SchemaInstanceLoader = _load_current_schema_instance,
        paper_metadata_loader: PaperMetadataLoader = read_paper_metadata,
        store_factory: StoreFactory | None = None,
        composition_factory: CompositionFactory = create_production_wiki_composition,
        wiki_storage_root: Path | str | None = None,
        max_proposals: int = 100,
    ) -> None:
        self.schema_definition_loader = schema_definition_loader
        self.schema_instance_loader = schema_instance_loader
        self.paper_metadata_loader = paper_metadata_loader
        self.store_factory = store_factory or (lambda context: WikiStore(context, wiki_storage_root))
        self.composition_factory = composition_factory
        self.max_proposals = max_proposals

    def load_build_inputs(self, context: WorkspaceContext) -> WorkspaceWikiBuildInputs:
        """Load and validate all assets before creating any Wiki mutation boundary."""
        if not isinstance(context, WorkspaceContext):
            raise WikiBuildInputError("invalid_input", "context must be a WorkspaceContext")
        definition = self._load_definition(context)
        instances: dict[str, SchemaInstance] = {}
        metadata: dict[str, PaperMetadata] = {}
        for paper_id in context.paper_ids:
            instances[paper_id] = self._load_instance(context, paper_id)
            metadata[paper_id] = self._load_metadata(context, paper_id)
        return WorkspaceWikiBuildInputs(
            context=context,
            definition=definition,
            instances_by_paper=instances,
            metadata_by_paper=metadata,
        )

    def build_wiki_for_workspace(
        self, context: WorkspaceContext
    ) -> WorkspaceWikiApplicationBuildResult:
        """Build and finalize a Workspace Wiki from freshly loaded authoritative assets."""
        inputs = self.load_build_inputs(context)
        store = self.store_factory(context)
        if not isinstance(store, WikiStore) or store.context != context:
            raise WikiBuildInputError("workspace_mismatch", "Wiki store is not bound to the requested workspace")
        composition = self.composition_factory(context, store)
        if not isinstance(composition, WikiProductionComposition) or composition.service.context != context:
            raise WikiBuildInputError("workspace_mismatch", "Wiki composition is not bound to the requested workspace")
        build = build_wiki_for_workspace(
            context,
            inputs.definition,
            inputs.instances_by_paper,
            inputs.metadata_by_paper,
            composition.service,
            composition.proposal_runner,
            composition.resolver,
            max_proposals=self.max_proposals,
        )
        return self._finalize_build(context, build, store, composition)

    @staticmethod
    def _finalize_build(
        context: WorkspaceContext,
        build: WorkspaceWikiBuildResult,
        store: WikiStore,
        composition: WikiProductionComposition,
    ) -> WorkspaceWikiApplicationBuildResult:
        """Persist and validate every derived artifact before returning to callers."""
        manifest = store.upsert_manifest(WikiManifest(
            workspace_id=context.workspace_id,
            schema_id=context.schema_id,
            schema_version=context.schema_version,
            paper_ids=context.paper_ids,
            builder_version=WIKI_BUILDER_VERSION,
            build_status=_manifest_build_status(build),
        ))
        try:
            index = composition.service.rebuild_indexes()
        except WikiIndexError as error:
            fingerprint = composition.service._source_fingerprint()
            index = IndexRebuildResult(status="failed", source_fingerprint=fingerprint, index_version=0, error_code=error.code)
        audit = composition.service.audit_wiki()
        blocking_vector_issues = {
            "embedding_unavailable",
            "embedding_provider_failure",
            "vector_index_missing",
            "vector_index_stale",
            "vector_index_incompatible",
        }
        has_blocking_vector_issue = any(issue.code in blocking_vector_issues for issue in audit.issues)
        complete = _manifest_build_status(build) == "complete" and index.status == "rebuilt" and audit.ok and not has_blocking_vector_issue
        finalized = manifest.model_copy(update={"build_status": "complete" if complete else "partial"})
        if finalized.build_status != manifest.build_status:
            manifest = store.upsert_manifest(finalized)
        return WorkspaceWikiApplicationBuildResult(
            build=build,
            manifest=manifest,
            index=index,
            audit=audit,
        )

    def _load_definition(self, context: WorkspaceContext) -> SchemaDefinition:
        try:
            definition = self.schema_definition_loader(context.schema_id)
        except Exception as error:
            raise WikiBuildInputError("missing_input", "schema definition could not be loaded") from error
        if not isinstance(definition, SchemaDefinition):
            raise WikiBuildInputError("invalid_input", "schema definition is malformed")
        if definition.schema_id != context.schema_id or definition.version != context.schema_version:
            raise WikiBuildInputError("schema_mismatch", "schema definition does not match the workspace")
        return definition

    def _load_instance(self, context: WorkspaceContext, paper_id: str) -> SchemaInstance:
        try:
            instance = self.schema_instance_loader(paper_id, context.schema_id)
        except Exception as error:
            raise WikiBuildInputError("missing_input", f"schema instance is missing for paper {paper_id!r}") from error
        if instance is None:
            raise WikiBuildInputError("missing_input", f"schema instance is missing for paper {paper_id!r}")
        if not isinstance(instance, SchemaInstance):
            raise WikiBuildInputError("invalid_input", f"schema instance is malformed for paper {paper_id!r}")
        if instance.paper_id != paper_id:
            raise WikiBuildInputError("paper_mismatch", f"schema instance is foreign to paper {paper_id!r}")
        if instance.schema_id != context.schema_id or instance.schema_version != context.schema_version:
            raise WikiBuildInputError("schema_mismatch", f"schema instance does not match paper {paper_id!r}")
        return instance

    def _load_metadata(self, context: WorkspaceContext, paper_id: str) -> PaperMetadata:
        try:
            metadata = self.paper_metadata_loader(paper_id)
        except Exception as error:
            raise WikiBuildInputError("missing_input", f"metadata is missing for paper {paper_id!r}") from error
        if not isinstance(metadata, PaperMetadata):
            raise WikiBuildInputError("missing_input", f"metadata is missing or malformed for paper {paper_id!r}")
        if metadata.paper_id != paper_id or metadata.paper_id not in context.paper_ids:
            raise WikiBuildInputError("paper_mismatch", f"metadata is foreign to paper {paper_id!r}")
        return metadata
