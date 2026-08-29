"""Layer3 Stage1 gateway L2S1 evidence delegation tests (T-005 / AC-019).

Proves with the REAL L2S1 public pipeline (fake parser + ``parse_paper`` +
``build_retrieval``): the bound gateway's evidence search/read operations
delegate to the existing L2S1 public APIs (``search_bm25`` / ``read_blocks``)
and return normalized Layer3 results identical to calling those APIs
directly, without duplicating the retrieval implementation (AC-019 / REQ-010 /
AC-024). Cross-Workspace RAG isolation is asserted both with the real
delegate and with a recording seam proving non-member calls never reach the
lower layer (AC-018).
"""

from __future__ import annotations

import pytest

from transit_scholar.layer2 import build_retrieval
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.retrieval.api import read_blocks as l2_read_blocks
from transit_scholar.layer2.retrieval.api import search_bm25 as l2_search_bm25
from transit_scholar.layer3.knowledge import L2S1EvidenceDelegate
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceKnowledgeGateway,
    WorkspaceService,
)
from tests.l2s1_fixtures import make_ready_paper, patch_parsers

DEFAULT_ITEMS = [
    make_item(
        item_id="h1", item_type="heading", text="Method", order=0, page=1,
        level=1, bbox=[70, 60, 530, 80],
    ),
    make_item(
        item_id="p1", item_type="paragraph",
        text="Reinforcement learning trains the holding controller.",
        order=1, page=1, bbox=[70, 100, 530, 120],
    ),
    make_item(
        item_id="p2", item_type="paragraph",
        text="Deep neural networks approximate the value function.",
        order=2, page=1, bbox=[70, 140, 530, 160],
    ),
    make_item(
        item_id="p3", item_type="paragraph",
        text="Bus headway regularity is measured by waiting time.",
        order=3, page=1, bbox=[70, 180, 530, 200],
    ),
]


def build_l2s1_paper(project_tmp_path, monkeypatch, l2_config):
    """Global Paper with real L2S1 canonical parse + built retrieval index.

    Returns ``(paper_id, parse_run_id)``. The Paper row is committed through
    the real session (``make_ready_paper``) BEFORE the transactional
    ``session`` fixture performs any statement, so the fixture's deferred
    snapshot sees it (see the L2S1 test-suite convention).
    """
    paper_id, _file_id, _pdf = make_ready_paper(
        project_tmp_path, title="Gateway Evidence Paper"
    )
    patch_parsers(
        monkeypatch, [FakeParserAdapter(items=DEFAULT_ITEMS, page_count=1)]
    )
    from transit_scholar.layer2.pipeline import parse_paper

    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    build_retrieval(paper_id, config=l2_config)
    return paper_id, result.parse_run_id


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
        raise AssertionError(
            f"L2S1 search must not be called for non-member {paper_id!r} (AC-018)"
        )

    def read_blocks(self, paper_id, block_ids):
        self.read_calls.append((paper_id, list(block_ids)))
        raise AssertionError(
            f"L2S1 read must not be called for non-member {paper_id!r} (AC-018)"
        )


# ---------------------------------------------------------------------------
# AC-019: member-Paper evidence reads delegate to the existing L2S1 APIs
# ---------------------------------------------------------------------------


def test_search_evidence_delegates_to_public_bm25_api(
    session, project_tmp_path, monkeypatch, l2_config
):
    paper_id, _run_id = build_l2s1_paper(project_tmp_path, monkeypatch, l2_config)
    service = WorkspaceService(session)
    workspace = service.create(name="Evidence Bound").workspace
    service.add_paper(workspace.workspace_id, paper_id)

    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )

    direct = l2_search_bm25(paper_id, "reinforcement", config=l2_config)
    via_gateway = gateway.search_evidence(paper_id, "reinforcement")

    # Delegation: identical normalized results, same envelope type, no
    # retrieval logic reimplemented inside Layer3.
    assert via_gateway.status == direct.status == "ok"
    assert via_gateway.method == direct.method
    assert [(h.chunk_id, h.rank, h.score) for h in via_gateway.hits] == [
        (h.chunk_id, h.rank, h.score) for h in direct.hits
    ]
    assert type(via_gateway) is type(direct)

    # The Paper view derives L2S1 readiness from the same global assets.
    view = gateway.get_paper(paper_id)
    assert view.l2s1_ready is True


def test_read_evidence_delegates_to_public_read_blocks_api(
    session, project_tmp_path, monkeypatch, l2_config
):
    paper_id, _run_id = build_l2s1_paper(project_tmp_path, monkeypatch, l2_config)
    service = WorkspaceService(session)
    workspace = service.create(name="Read Bound").workspace
    service.add_paper(workspace.workspace_id, paper_id)

    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )

    # Block ids come from the canonical parse artifacts of the global Paper.
    import json

    from transit_scholar.layer2.paths import run_paths

    rp = run_paths(l2_config, paper_id, _run_id)
    block_ids = [
        json.loads(line)["block_id"]
        for line in rp.blocks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][:2]
    assert block_ids, "expected canonical blocks for the parsed paper"

    direct = l2_read_blocks(paper_id, block_ids, config=l2_config)
    via_gateway = gateway.read_evidence(paper_id, block_ids)
    assert via_gateway == direct
    assert {record["block_id"] for record in via_gateway} == set(block_ids)


def test_search_evidence_passes_top_k_and_filters_through(
    session, project_tmp_path, monkeypatch, l2_config
):
    paper_id, run_id = build_l2s1_paper(project_tmp_path, monkeypatch, l2_config)
    service = WorkspaceService(session)
    workspace = service.create(name="Passthrough").workspace
    service.add_paper(workspace.workspace_id, paper_id)

    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )

    limited = gateway.search_evidence(paper_id, "reinforcement", top_k=2)
    assert len(limited.hits) <= 2
    direct_limited = l2_search_bm25(
        paper_id, "reinforcement", top_k=2, config=l2_config
    )
    assert [(h.chunk_id, h.score) for h in limited.hits] == [
        (h.chunk_id, h.score) for h in direct_limited.hits
    ]

    filtered = gateway.search_evidence(
        paper_id, "reinforcement", filters={"parse_run_id": run_id}
    )
    direct_filtered = l2_search_bm25(
        paper_id, "reinforcement", filters={"parse_run_id": run_id}, config=l2_config
    )
    assert [(h.chunk_id, h.score) for h in filtered.hits] == [
        (h.chunk_id, h.score) for h in direct_filtered.hits
    ]


# ---------------------------------------------------------------------------
# cross-Workspace RAG isolation (AC-015/AC-018)
# ---------------------------------------------------------------------------


def test_cross_workspace_rag_isolation_with_real_delegate(
    session, project_tmp_path, monkeypatch, l2_config
):
    paper_id, _run_id = build_l2s1_paper(project_tmp_path, monkeypatch, l2_config)
    service = WorkspaceService(session)
    ws_a = service.create(name="A", workspace_id="rags-a").workspace
    ws_b = service.create(name="B", workspace_id="rags-b").workspace
    service.add_paper(ws_a.workspace_id, paper_id)
    service.add_paper(ws_b.workspace_id, paper_id)

    delegate = L2S1EvidenceDelegate(config=l2_config)
    gateway_a = WorkspaceKnowledgeGateway(
        session, workspace_id=ws_a.workspace_id, data_root=project_tmp_path,
        evidence=delegate,
    )
    gateway_b = WorkspaceKnowledgeGateway(
        session, workspace_id=ws_b.workspace_id, data_root=project_tmp_path,
        evidence=delegate,
    )
    assert gateway_a.search_evidence(paper_id, "reinforcement").status == "ok"
    assert gateway_b.search_evidence(paper_id, "reinforcement").status == "ok"

    # Revoke A's membership only: A loses the Paper immediately (AC-015) while
    # B — whose membership is independent — keeps delegating.
    service.remove_paper(ws_a.workspace_id, paper_id)
    with pytest.raises(PaperNotMemberError) as exc_info:
        gateway_a.search_evidence(paper_id, "reinforcement")
    assert exc_info.value.code == "paper_not_member"
    assert gateway_b.search_evidence(paper_id, "reinforcement").status == "ok"
    assert gateway_b.get_paper(paper_id).paper_id == paper_id


def test_non_member_with_ready_assets_never_reaches_l2s1(
    session, project_tmp_path, monkeypatch, l2_config
):
    """The Paper HAS full L2S1 assets, but membership is the gate (AC-018):
    a non-member cannot read them, and zero lower-layer calls occur."""
    paper_id, _run_id = build_l2s1_paper(project_tmp_path, monkeypatch, l2_config)
    service = WorkspaceService(session)
    ws_b = service.create(name="B").workspace
    service.add_paper(service.create(name="A").workspace.workspace_id, paper_id)

    recording = RecordingEvidence()
    gateway_b = WorkspaceKnowledgeGateway(
        session, workspace_id=ws_b.workspace_id, data_root=project_tmp_path,
        evidence=recording,
    )
    with pytest.raises(PaperNotMemberError):
        gateway_b.search_evidence(paper_id, "reinforcement")
    with pytest.raises(PaperNotMemberError):
        gateway_b.read_evidence(paper_id, ["b1"])
    assert recording.search_calls == []
    assert recording.read_calls == []
    assert recording.ready_calls == []