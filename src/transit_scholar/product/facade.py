from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from transit_scholar.config import settings
from transit_scholar.db.models import PaperFile, Workspace, WorkspacePaperMembership, AgentRun
from transit_scholar.identity import (
    list_duplicate_candidates,
    resolve_duplicate,
    restore_paper,
    soft_delete_paper,
    update_paper_metadata,
)
from transit_scholar.workflow import reconcile_paper, run_import_pipeline
from transit_scholar.workflow import get_paper, get_second_layer_input, list_papers
from transit_scholar.workflow.readers import list_metadata_candidates
from transit_scholar.citation.service import list_citation_records
from transit_scholar.doi_enrichment.service import collect_provider_results, refresh_enrichment

from .conversation import ConversationService
from .projection import ProductStateProjector
from .research import ResearchService
from transit_scholar.layer2.schema_catalog import SchemaCatalog
from .errors import ProductConflictError


class PaperInUseError(ProductConflictError):
    """Raised when an active Workspace still references a library paper."""

    def __init__(self, paper_id: str, workspace_ids: list[str] | None = None):
        super().__init__(f"Paper is in use by active workspaces: {paper_id}")
        self.paper_id = paper_id
        self.workspace_ids = list(workspace_ids or [])


class WorkspaceBusyError(ProductConflictError):
    """Raised when a run still depends on a Workspace revision."""


@dataclass(frozen=True)
class RegisteredPaperFile:
    file_id: str
    paper_id: str
    filename: str
    content_type: str
    path: Path


class TransitScholarProduct:
    def __init__(self, session, runtime_factory, *, goal_resolver=None, data_root=None, schema_catalog=None, settings_obj=None, session_factory=None):
        self.session = session
        self.session_factory = session_factory or sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
        self.settings = settings_obj or settings
        self.data_root = Path(data_root or self.settings.data_root)
        self.conversations = ConversationService(session)
        self.research = ResearchService(session, runtime_factory, conversations=self.conversations, goal_resolver=goal_resolver)
        self.projector = ProductStateProjector(session, runtime_factory)
        self.schema_catalog = schema_catalog or SchemaCatalog(self.data_root)

    def describe_schema(self, definition):
        return self.schema_catalog.describe(definition)

    def resolve_schema(self, schema_id, version=None):
        return self.schema_catalog.resolve(schema_id, version)

    def create_workspace(self, name, schema_id=None, schema_version=None, workspace_id=None):
        from transit_scholar.layer3.workspace import WorkspaceService
        definition = self.resolve_schema(schema_id, schema_version) if schema_id else None
        result = WorkspaceService(self.session).create(name=name, schema_definition=definition, workspace_id=workspace_id)
        self.session.commit()
        return result.workspace

    def list_workspaces(self):
        from transit_scholar.layer3.workspace import WorkspaceService
        return WorkspaceService(self.session).list_workspaces()

    def get_workspace(self, workspace_id):
        from transit_scholar.layer3.workspace import WorkspaceService
        return WorkspaceService(self.session).get(workspace_id)

    def _guard_workspace_mutation(self, workspace_id):
        busy = self.session.execute(select(AgentRun.id).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.status.in_(("created", "running", "paused")),
        )).first()
        if busy:
            raise WorkspaceBusyError(workspace_id)

    def archive_workspace(self, workspace_id):
        self._guard_workspace_mutation(workspace_id)
        from transit_scholar.layer3.workspace import WorkspaceService
        result = WorkspaceService(self.session).archive(workspace_id)
        self.session.commit()
        return result.workspace

    def delete_workspace(self, workspace_id):
        self._guard_workspace_mutation(workspace_id)
        from transit_scholar.layer3.workspace import WorkspaceService
        return WorkspaceService(self.session).delete(workspace_id, data_root=self.data_root).workspace

    def list_workspace_papers(self, workspace_id):
        from transit_scholar.layer3.workspace import WorkspaceService
        memberships = WorkspaceService(self.session).list_memberships(workspace_id)
        return memberships

    def add_workspace_paper(self, workspace_id, paper_id):
        self._guard_workspace_mutation(workspace_id)
        from transit_scholar.layer3.workspace import WorkspaceService
        result = WorkspaceService(self.session).add_paper(workspace_id, paper_id)
        self.session.commit()
        return result

    def remove_workspace_paper(self, workspace_id, paper_id):
        self._guard_workspace_mutation(workspace_id)
        from transit_scholar.layer3.workspace import WorkspaceService
        result = WorkspaceService(self.session).remove_paper(workspace_id, paper_id)
        self.session.commit()
        return result

    def workspace_schema(self, workspace_id):
        from transit_scholar.layer3.workspace import WorkspaceService
        return WorkspaceService(self.session).schema_binding(workspace_id)

    def workspace_schema_readiness(self, workspace_id, paper_id=None):
        from transit_scholar.layer3.schema import WorkspaceSchemaService
        result = WorkspaceSchemaService(
            self.session, data_root=self.data_root
        ).paper_schema_readiness(workspace_id, [paper_id] if paper_id else None)
        return result

    def materialize_workspace_schema(self, workspace_id, paper_id, **options):
        from transit_scholar.layer3.schema import WorkspaceSchemaService
        return WorkspaceSchemaService(
            self.session, data_root=self.data_root
        ).materialize(workspace_id, paper_id, **options)

    def _workspace_wiki(self):
        from transit_scholar.layer3.wiki import WorkspaceWikiService
        from transit_scholar.metadata.service import read_paper_metadata
        return WorkspaceWikiService(
            self.session, data_root=self.data_root,
            paper_metadata_loader=lambda paper_id: read_paper_metadata(paper_id, session_factory=self.session_factory),
        )

    def workspace_wiki_status(self, workspace_id):
        return self._workspace_wiki().status(workspace_id)

    def workspace_wiki_capability(self, workspace_id):
        return self._workspace_wiki().capability(workspace_id)

    def build_workspace_wiki(self, workspace_id):
        return self._workspace_wiki().build(workspace_id)

    def _read_workspace_wiki(self, workspace_id):
        from transit_scholar.layer3.wiki.errors import (
            WikiCorruptError, WikiMissingError, WikiStaleError,
        )

        wiki = self._workspace_wiki()
        status = wiki.status(workspace_id)
        errors = {
            "missing": WikiMissingError,
            "stale": WikiStaleError,
            "error": WikiCorruptError,
        }
        error_type = errors.get(status.status)
        if error_type is not None:
            raise error_type(f"Workspace Base Wiki is {status.status}")
        return wiki

    def list_workspace_wiki_pages(self, workspace_id):
        wiki = self._read_workspace_wiki(workspace_id)
        return wiki.get_wiki_service(workspace_id).store.list_pages()

    def get_workspace_wiki_page(self, workspace_id, page_id):
        wiki = self._read_workspace_wiki(workspace_id)
        return wiki.get_wiki_service(workspace_id).get_page(page_id)

    def list_workspace_wiki_entities(self, workspace_id):
        wiki = self._read_workspace_wiki(workspace_id)
        return wiki.get_wiki_service(workspace_id).store.list_entities()

    def get_workspace_wiki_entity(self, workspace_id, entity_id):
        wiki = self._read_workspace_wiki(workspace_id)
        return wiki.get_wiki_service(workspace_id).get_entity(entity_id)

    def list_workspace_agentic_entries(self, workspace_id, *, include_stale=False):
        wiki = self._workspace_wiki()
        self.get_workspace(workspace_id)
        return wiki._agentic_store_for_workspace(workspace_id).list(
            workspace_id, include_stale=include_stale
        )

    def get_workspace_agentic_entry(self, workspace_id, entry_id):
        wiki = self._workspace_wiki()
        self.get_workspace(workspace_id)
        return wiki._agentic_store_for_workspace(workspace_id).get(entry_id, workspace_id)

    def search_workspace_wiki(self, workspace_id, query, *, limit=20, mode="lexical", include_stale=False):
        return self._workspace_wiki().search(
            workspace_id, query, limit=limit, mode=mode, include_stale=include_stale
        )

    def create_conversation(self, workspace_id, title=None):
        conversation = self.conversations.create_session(workspace_id, title)
        self.session.commit()
        return conversation
    create_session = create_conversation
    def list_conversations(self, workspace_id): return self.conversations.list_sessions(workspace_id)
    def get_conversation(self, conversation_id):
        return self.conversations.get_session(conversation_id)

    def read_turn(self, turn_id):
        return self.conversations.get_turn(turn_id)

    def answer_citations(self, agent_run_id, final_response):
        return self.projector.answer_citations(agent_run_id, final_response)

    def reconcile_interrupted_runs(self, active_run_ids=()):
        """Durably pause runs left running by a previous local process."""
        active_run_ids = set(active_run_ids)
        statement = select(AgentRun).where(AgentRun.status == "running")
        if active_run_ids:
            statement = statement.where(AgentRun.id.not_in(active_run_ids))

        reconciled_ids = []
        for run in self.session.execute(statement).scalars():
            self.research.execution.update_agent_run_status(run.id, "paused")
            reconciled_ids.append(run.id)
        if reconciled_ids:
            self.session.commit()
        return reconciled_ids

    def list_turns(self, conversation_id):
        return self.conversations.list_turns(conversation_id)
    def read_conversation(self, conversation_id): return self.projector.conversation(conversation_id)
    read_conversation_state = read_conversation
    conversation_state = read_conversation
    def prepare_message(self, conversation_id, message): return self.research.prepare_message(conversation_id, message)
    def discard_prepared_message(self, prepared): return self.research.discard_prepared_message(prepared)
    def submit_message(self, conversation_id, message): return self.research.submit_message(conversation_id, message)
    def execute_run(self, agent_run_id, **kwargs): return self.research.execute_run(agent_run_id, **kwargs)
    def request_pause(self, agent_run_id): return self.research.request_pause(agent_run_id)
    def resume_run(self, agent_run_id): return self.research.resume_run(agent_run_id)
    def read_run_state(self, agent_run_id): return self.projector.run_state(agent_run_id)
    def read_run_timeline(self, agent_run_id, after_sequence=0):
        return self.projector.run_timeline(agent_run_id, after_sequence)

    def import_paper(self, upload_path: str | Path):
        return run_import_pipeline(upload_path, session_factory=self.session_factory, data_root=self.data_root, settings_obj=self.settings)

    def list_library_papers(self, **filters):
        return list_papers(session_factory=self.session_factory, **filters)

    def read_paper(self, paper_id: str):
        return get_paper(paper_id, session_factory=self.session_factory)

    def read_second_layer_input(self, paper_id: str):
        return get_second_layer_input(paper_id, session_factory=self.session_factory, data_root=self.data_root)

    def reconcile_paper(self, paper_id: str):
        return reconcile_paper(paper_id, session_factory=self.session_factory, data_root=self.data_root)

    def update_paper_metadata(self, paper_id: str, fields: dict[str, object]):
        return update_paper_metadata(paper_id, fields, session_factory=self.session_factory)

    def metadata_candidates(self, paper_id: str):
        return list_metadata_candidates(paper_id=paper_id, session_factory=self.session_factory)

    def enrichment(self, paper_id: str):
        return collect_provider_results(paper_id, session_factory=self.session_factory)

    def refresh_enrichment(self, paper_id: str):
        return refresh_enrichment(paper_id, session_factory=self.session_factory)

    def duplicate_relations(self, paper_id: str):
        return list_duplicate_candidates(paper_id, status=None, session_factory=self.session_factory)

    def resolve_duplicate_relation(self, relation_id: str, decision: str):
        return resolve_duplicate(relation_id, decision, session_factory=self.session_factory)

    def bibliography_citations(self, paper_id: str):
        return list_citation_records(paper_id, session_factory=self.session_factory)

    def soft_delete_library_paper(self, paper_id: str):
        active_memberships = self.session.execute(
            select(WorkspacePaperMembership.workspace_id)
            .join(Workspace, Workspace.id == WorkspacePaperMembership.workspace_id)
            .where(
                WorkspacePaperMembership.paper_id == paper_id,
                Workspace.status == "active",
            )
        ).scalars().all()
        if active_memberships:
            raise PaperInUseError(paper_id, list(active_memberships))
        return soft_delete_paper(paper_id, session_factory=self.session_factory, data_root=self.data_root)

    def restore_library_paper(self, paper_id: str):
        return restore_paper(paper_id, session_factory=self.session_factory, data_root=self.data_root)

    def list_registered_paper_files(self, paper_id: str) -> list[dict[str, object]]:
        rows = self.session.execute(
            select(PaperFile).where(
                PaperFile.paper_id == paper_id,
                PaperFile.deleted_at.is_(None),
            )
        ).scalars().all()
        return [
            {
                "file_id": row.id,
                "original_filename": row.original_filename,
                "mime_type": row.mime_type,
                "file_size_bytes": row.file_size_bytes,
                "is_primary": row.is_primary,
                "page_count": row.page_count,
            }
            for row in rows
        ]

    def resolve_registered_pdf(self, file_id: str) -> RegisteredPaperFile | None:
        row = self.session.get(PaperFile, file_id)
        if row is None or row.deleted_at is not None or not row.paper_id or not row.relative_path:
            return None
        root = self.data_root.resolve()
        path = (root / row.relative_path).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return RegisteredPaperFile(
            file_id=row.id,
            paper_id=row.paper_id,
            filename=row.original_filename or "paper.pdf",
            content_type=row.mime_type or "application/pdf",
            path=path,
        )

    def close(self):
        """Release the facade's long-lived SQLAlchemy session."""
        self.session.close()
