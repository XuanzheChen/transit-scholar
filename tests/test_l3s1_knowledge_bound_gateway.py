"""Layer3 Stage1 bound knowledge gateway tests (T-005 / REQ-011 / AC-022).

Proves the workspace-bound gateway surface itself:

- AC-022: the gateway is constructed with the Workspace identity and its
  upper-layer public methods NEVER accept or override a workspace_id — the
  Workspace is bound once at construction;
- AC-018: Paper-scoped operations for a non-member Paper fail with the
  explicit ``paper_not_member`` outcome BEFORE any lower-layer Paper or
  retrieval operation executes (recording fakes prove zero delegation);
- REQ-011 / C-006: Workspace visibility is enforced in code — a Paper visible
  in Workspace A is invisible to Workspace B's bound gateway;
- AC-016/AC-017: non-active lifecycle states reject every normal knowledge
  operation with ``workspace_not_active``.
"""

from __future__ import annotations

import inspect

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema import RetrievalResult
from transit_scholar.layer3.knowledge import (
    L2S1EvidenceDelegate,
    WorkspaceKnowledgeGateway,
)
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceNotActiveError,
    WorkspaceService,
)

#: Every upper-layer public operation of the bound gateway, by name.
#: None of them may take a workspace identifier (AC-022).
PUBLIC_OPERATIONS = (
    "current_state",
    "list_papers",
    "get_paper",
    "search_evidence",
    "read_evidence",
    "get_schema_instance",
    "get_schema_field",
    "wiki_status",
    "search_wiki",
)


class RecordingEvidence:
    """Recording L2S1 seam; proves access decisions happen before delegation."""

    def __init__(self) -> None:
        self.ready_calls: list[str] = []
        self.search_calls: list[tuple[str, str]] = []
        self.read_calls: list[tuple[str, list[str]]] = []

    def l2s1_ready(self, paper_id: str) -> bool:
        self.ready_calls.append(paper_id)
        return True

    def search(self, paper_id, query, *, top_k=20, filters=None):
        self.search_calls.append((paper_id, query))
        return RetrievalResult(status="ok", method="bm25", hits=[])

    def read_blocks(self, paper_id, block_ids):
        self.read_calls.append((paper_id, list(block_ids)))
        return []


class RecordingSchema:
    """Recording Schema seam; proves the membership gate fires first."""

    def __init__(self) -> None:
        self.instance_calls: list[str] = []
        self.field_calls: list[tuple[str, str]] = []

    def get_instance(self, workspace_id, paper_id, *, run_id=None):
        self.instance_calls.append(paper_id)
        raise AssertionError("schema read must not run for a non-member")

    def get_field(self, workspace_id, paper_id, field_id, *, run_id=None):
        self.field_calls.append((paper_id, field_id))
        raise AssertionError("schema read must not run for a non-member")

    def current_run_identities(self, workspace_id, paper_ids=None):
        return {paper_id: None for paper_id in (paper_ids or [])}


class RecordingWiki:
    """Recording Wiki seam; proves the bound Workspace is the only target."""

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str]] = []
        self.status_calls: list[str] = []

    def status(self, workspace_id):
        self.status_calls.append(workspace_id)
        return type(
            "Status",
            (),
            {"status": "missing", "workspace_id": workspace_id},
        )()

    def search(self, workspace_id, query, *, limit=20, mode="lexical"):
        self.search_calls.append((workspace_id, query))
        raise AssertionError("wiki search must not be reached in this test")


def add_paper(session, paper_id: str, title: str = "Gateway Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def make_gateway(session, project_tmp_path, workspace_id, *, evidence=None, schemas=None, wiki=None):
    return WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        data_root=project_tmp_path,
        evidence=evidence,
        schemas=schemas,
        wiki=wiki,
    )


# ---------------------------------------------------------------------------
# AC-022: bound object; public operations never take/override workspace_id
# ---------------------------------------------------------------------------


def test_public_operations_never_accept_workspace_id_parameter():
    """The gateway binds the Workspace at construction (AC-022): no public
    method accepts a workspace_id, and construction requires one."""
    signature = inspect.signature(WorkspaceKnowledgeGateway.__init__)
    assert "workspace_id" in signature.parameters
    assert signature.parameters["workspace_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["workspace_id"].default is inspect.Parameter.empty

    for name in PUBLIC_OPERATIONS:
        parameters = inspect.signature(
            getattr(WorkspaceKnowledgeGateway, name)
        ).parameters
        assert "workspace_id" not in parameters, (
            f"bound operation {name!r} must not accept a workspace_id override "
            "(AC-022)"
        )
        assert "workspace" not in parameters

    # Search/read operations scope the Paper only; Wiki and state operations
    # take no identifier at all.
    assert "paper_id" in inspect.signature(WorkspaceKnowledgeGateway.get_paper).parameters
    assert "paper_id" in inspect.signature(WorkspaceKnowledgeGateway.search_evidence).parameters
    assert "paper_id" in inspect.signature(WorkspaceKnowledgeGateway.read_evidence).parameters
    for name in ("wiki_status", "search_wiki", "list_papers", "current_state"):
        parameters = inspect.signature(getattr(WorkspaceKnowledgeGateway, name)).parameters
        assert not any(key in ("paper_id", "workspace_id") for key in parameters)


def test_gateway_binds_workspace_identity_at_construction(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Bound").workspace
    gateway = make_gateway(session, project_tmp_path, workspace.workspace_id)
    assert gateway.workspace_id == workspace.workspace_id
    assert gateway.current_state().workspace_id == workspace.workspace_id
    # The same gateway object can never be pointed at another Workspace.
    other = service.create(name="Other").workspace
    assert [view.paper_id for view in gateway.list_papers()] == []
    assert gateway.workspace_id != other.workspace_id


# ---------------------------------------------------------------------------
# cross-Workspace Paper isolation (REQ-011 / AC-018 paper path)
# ---------------------------------------------------------------------------


def test_paper_visibility_is_isolated_per_workspace(session, project_tmp_path):
    service = WorkspaceService(session)
    ws_a = service.create(name="A", workspace_id="gw-ws-a").workspace
    ws_b = service.create(name="B", workspace_id="gw-ws-b").workspace
    paper = add_paper(session, "gw-p1")
    service.add_paper(ws_a.workspace_id, paper.id)

    evidence = RecordingEvidence()
    gateway_a = make_gateway(session, project_tmp_path, ws_a.workspace_id, evidence=evidence)
    gateway_b = make_gateway(session, project_tmp_path, ws_b.workspace_id, evidence=evidence)

    # Workspace A sees the Paper; Workspace B does not.
    assert [view.paper_id for view in gateway_a.list_papers()] == [paper.id]
    assert gateway_a.get_paper(paper.id).paper_id == paper.id
    assert gateway_b.list_papers() == []
    with pytest.raises(PaperNotMemberError) as exc_info:
        gateway_b.get_paper(paper.id)
    assert exc_info.value.code == "paper_not_member"

    # The membership gate fires before the Paper row is even looked up: a
    # non-member Paper that does not exist anywhere still reports
    # paper_not_member, never paper_not_found.
    with pytest.raises(PaperNotMemberError):
        gateway_b.get_paper("no-such-paper")


def test_paper_read_for_non_member_never_touches_paper_row_or_evidence(
    session, project_tmp_path
):
    """AC-018 paper path: get_paper for a non-member must not delegate to the
    lower-layer Paper read (the DB Paper row) or the evidence readiness probe."""
    service = WorkspaceService(session)
    ws_b = service.create(name="B").workspace
    paper = add_paper(session, "gw-hidden")
    service.add_paper(service.create(name="A").workspace.workspace_id, paper.id)

    evidence = RecordingEvidence()
    gateway_b = make_gateway(session, project_tmp_path, ws_b.workspace_id, evidence=evidence)
    with pytest.raises(PaperNotMemberError):
        gateway_b.get_paper(paper.id)
    # No evidence readiness probe ran for the rejected Paper either.
    assert evidence.ready_calls == []


# ---------------------------------------------------------------------------
# AC-018: non-member evidence calls rejected before lower-layer delegation
# ---------------------------------------------------------------------------


def test_non_member_evidence_search_and_read_never_delegate(session, project_tmp_path):
    service = WorkspaceService(session)
    ws_a = service.create(name="A").workspace
    ws_b = service.create(name="B").workspace
    paper = add_paper(session, "gw-evidence")
    service.add_paper(ws_a.workspace_id, paper.id)

    evidence = RecordingEvidence()
    gateway_b = make_gateway(session, project_tmp_path, ws_b.workspace_id, evidence=evidence)

    with pytest.raises(PaperNotMemberError) as search_error:
        gateway_b.search_evidence(paper.id, "controller type")
    assert search_error.value.code == "paper_not_member"
    with pytest.raises(PaperNotMemberError) as read_error:
        gateway_b.read_evidence(paper.id, ["b1"])
    assert read_error.value.code == "paper_not_member"

    assert evidence.search_calls == []
    assert evidence.read_calls == []

    # The member Workspace's gateway delegates normally with the same seam.
    gateway_a = make_gateway(session, project_tmp_path, ws_a.workspace_id, evidence=evidence)
    gateway_a.search_evidence(paper.id, "controller type")
    gateway_a.read_evidence(paper.id, ["b1"])
    assert evidence.search_calls == [(paper.id, "controller type")]
    assert evidence.read_calls == [(paper.id, ["b1"])]


def test_non_member_schema_and_wiki_never_delegate(session, project_tmp_path):
    """The membership gate fires before Schema delegation; Wiki operations
    target only the bound Workspace."""
    service = WorkspaceService(session)
    ws_a = service.create(name="A", workspace_id="gw-schema-owner").workspace
    ws_b = service.create(name="B", workspace_id="gw-schema-hidden").workspace
    paper = add_paper(session, "gw-schema-hidden")
    service.add_paper(ws_a.workspace_id, paper.id)

    schemas = RecordingSchema()
    wiki = RecordingWiki()
    gateway_b = make_gateway(
        session,
        project_tmp_path,
        ws_b.workspace_id,
        schemas=schemas,
        wiki=wiki,
    )
    with pytest.raises(PaperNotMemberError):
        gateway_b.get_schema_instance(paper.id)
    with pytest.raises(PaperNotMemberError):
        gateway_b.get_schema_field(paper.id, "controller_type")
    assert schemas.instance_calls == []
    assert schemas.field_calls == []

    # Wiki operations are Workspace-scoped, not Paper-scoped: they resolve
    # only the bound Workspace's own Wiki service (missing/unsupported here).
    assert gateway_b.wiki_status().status == "missing"
    assert wiki.status_calls == [ws_b.workspace_id]


# ---------------------------------------------------------------------------
# AC-016/AC-017: non-active Workspaces reject normal knowledge access
# ---------------------------------------------------------------------------


def test_archived_workspace_rejects_all_knowledge_operations(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Archive Gate").workspace
    paper = add_paper(session, "gw-archive")
    service.add_paper(workspace.workspace_id, paper.id)
    service.archive(workspace.workspace_id)

    gateway = make_gateway(session, project_tmp_path, workspace.workspace_id)
    for operation in (
        lambda: gateway.list_papers(),
        lambda: gateway.get_paper(paper.id),
        lambda: gateway.search_evidence(paper.id, "query"),
        lambda: gateway.read_evidence(paper.id, ["b1"]),
        lambda: gateway.get_schema_instance(paper.id),
        lambda: gateway.get_schema_field(paper.id, "controller_type"),
        lambda: gateway.wiki_status(),
        lambda: gateway.search_wiki("query"),
        lambda: gateway.current_state(),
    ):
        with pytest.raises(WorkspaceNotActiveError) as exc_info:
            operation()
        assert exc_info.value.code == "workspace_not_active"


def test_deleted_workspace_rejects_all_knowledge_operations(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Delete Gate").workspace
    paper = add_paper(session, "gw-delete")
    service.add_paper(workspace.workspace_id, paper.id)
    service.delete(workspace.workspace_id, data_root=project_tmp_path)

    gateway = make_gateway(session, project_tmp_path, workspace.workspace_id)
    with pytest.raises(WorkspaceNotActiveError) as exc_info:
        gateway.get_paper(paper.id)
    assert exc_info.value.code == "workspace_not_active"
    with pytest.raises(WorkspaceNotActiveError):
        gateway.search_evidence(paper.id, "query")
    with pytest.raises(WorkspaceNotActiveError):
        gateway.search_wiki("query")
    # The deleted Workspace's memberships are gone: no Paper is listed.
    with pytest.raises(WorkspaceNotActiveError):
        gateway.list_papers()


# ---------------------------------------------------------------------------
# public API surface stays L2S1-compatible (AC-024)
# ---------------------------------------------------------------------------


def test_gateway_default_evidence_is_the_public_l2s1_delegate():
    assert L2S1EvidenceDelegate is not None
    # The default delegate can be pointed at an isolated Layer2Config without
    # any Workspace identifier (AC-024: L2S1 stays independently usable).
    signature = inspect.signature(L2S1EvidenceDelegate.__init__)
    assert "workspace" not in str(signature)
    assert "config" in signature.parameters