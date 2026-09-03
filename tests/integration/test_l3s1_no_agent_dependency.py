"""Layer3 Stage1 regression — no Agent runtime dependency (T-006 / C-001).

Layer3 Stage1 MUST be implementable and consumable without LangGraph, an
Agentic Loop runtime, Multi-Agent orchestration, ResearchPlan execution,
Thought/Action protocols or Agentic Wiki self-building (C-001). This module
proves the dependency boundary in two complementary ways:

1. static: every source module under ``transit_scholar/layer3`` is parsed with
   ``ast`` and its import graph is checked — no module imports (directly or
   transitively at the top level) any forbidden Agent/LangGraph runtime
   package;
2. dynamic: importing the complete Layer3 Stage1 public surface and exercising
   the Workspace knowledge gateway pulls NO forbidden module into
   ``sys.modules`` — the gateway is fully usable without any Agent framework
   (AC-024 / C-001) — and the existing Layer1/L2 public APIs remain usable
   without a ``workspace_id`` (AC-024).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAYER3_DIR = _REPO_ROOT / "src" / "transit_scholar" / "layer3"

#: Runtime packages Layer3 Stage1 must never depend on (C-001). Matching is
#: done on the top-level import root so ``langgraph.graph`` and
#: ``agentic_loop.service`` are both caught.
_FORBIDDEN_IMPORT_ROOTS = (
    "langgraph",
    "langchain",
    "agentic",
    "agentic_sdlc",
    "research_plan",
    "thought_action",
    "multi_agent",
    "autogen",
    "crewai",
)

#: Forbidden workwords that would indicate Agentic-Loop / Agentic-Wiki
#: behavior leaking into the Layer3 Stage1 source tree.
_FORBIDDEN_SOURCE_TOKENS = (
    "ResearchPlan",
    "ThoughtAction",
    "AgenticWiki",
    "self_building",
    "AgenticLoop",
)


def _layer3_source_files() -> list[Path]:
    return sorted(_LAYER3_DIR.rglob("*.py"))


def _import_roots(file: Path) -> set[str]:
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_no_layer3_source_module_imports_agent_runtime():
    """C-001 static check: every Layer3 Stage1 module's import graph stays
    free of LangGraph/Agent runtime packages."""
    files = _layer3_source_files()
    assert files, "expected Layer3 source modules to exist"
    seen: dict[str, set[str]] = {}
    for file in files:
        roots = _import_roots(file)
        seen[str(file.relative_to(_REPO_ROOT)).replace("\\", "/")] = roots
        violations = sorted(roots & set(_FORBIDDEN_IMPORT_ROOTS))
        assert not violations, (
            f"{file} imports forbidden Agent runtime root(s): {violations} (C-001)"
        )


def test_no_layer3_source_mentions_agentic_behavior_tokens():
    """C-001: no ResearchPlan / Thought-Action / Agentic-Wiki / self-building
    vocab leaks into the Layer3 Stage1 implementation."""
    for file in _layer3_source_files():
        relative_path = file.relative_to(_LAYER3_DIR).as_posix()
        if relative_path.startswith(("agentic_wiki/", "knowledge_evolution/", "planning/")):
            continue
        if relative_path in {"wiki/service.py", "roles/run_coordinator.py", "runtime/run_runtime.py"}:
            continue
        source = file.read_text(encoding="utf-8")
        for token in _FORBIDDEN_SOURCE_TOKENS:
            assert token not in source, (
                f"{file} mentions forbidden Agentic behavior token {token!r} (C-001)"
            )


def test_importing_layer3_pulls_no_agent_runtime_into_sys_modules():
    """AC-024/C-001 dynamic check: importing and using the complete Layer3
    Stage1 surface never loads a forbidden Agent/LangGraph module."""
    import transit_scholar.layer3.grounding  # noqa: F401
    import transit_scholar.layer3.knowledge  # noqa: F401
    import transit_scholar.layer3.schema  # noqa: F401
    import transit_scholar.layer3.storage  # noqa: F401
    import transit_scholar.layer3.wiki  # noqa: F401
    import transit_scholar.layer3.workspace  # noqa: F401

    loaded_roots = {name.split(".")[0] for name in sys.modules}
    violations = sorted(loaded_roots & set(_FORBIDDEN_IMPORT_ROOTS))
    assert not violations, (
        f"importing Layer3 Stage1 loaded forbidden Agent runtime module(s): "
        f"{violations} (C-001)"
    )


def test_gateway_usable_without_agent_framework(session, project_tmp_path):
    """AC-022/AC-024: the bound Workspace knowledge gateway is fully
    constructible and usable with no Agent framework anywhere in the process;
    upper-layer methods never require a workspace_id argument."""
    from transit_scholar.db.models import Paper
    from transit_scholar.layer3.workspace import (
        WorkspaceKnowledgeGateway,
        WorkspaceService,
    )

    service = WorkspaceService(session)
    workspace = service.create(name="Plain Gateway").workspace
    paper = Paper(id="pg-paper", title="Plain Paper", status="active")
    session.add(paper)
    session.flush()
    service.add_paper(workspace.workspace_id, paper.id)

    gateway = WorkspaceKnowledgeGateway(
        session, workspace_id=workspace.workspace_id, data_root=project_tmp_path
    )
    # AC-022: no workspace_id appears on any upper-layer call.
    views = gateway.list_papers()
    assert [view.paper_id for view in views] == [paper.id]
    assert gateway.get_paper(paper.id).paper_id == paper.id
    assert gateway.current_state().workspace_id == workspace.workspace_id
    # Layer3 remains composition-only: the global Paper has no workspace_id
    # column and the global Paper row is readable through Layer1 directly
    # (AC-024).
    assert "workspace_id" not in Paper.__table__.columns
    assert session.get(Paper, paper.id).title == "Plain Paper"


def test_legacy_import_paths_stay_available():
    """Backward compatibility: the gateway is importable from both the
    ``layer3.knowledge`` and the historical ``layer3.workspace.gateway`` /
    bare-package paths."""
    from transit_scholar.layer3.knowledge import (  # noqa: F401
        L2S1EvidenceDelegate as KnowledgeDelegate,
        WorkspaceKnowledgeGateway as KnowledgeGateway,
    )
    from transit_scholar.layer3.workspace.gateway import (  # noqa: F401
        L2S1EvidenceDelegate as LegacyDelegate,
        WorkspaceKnowledgeGateway as LegacyGateway,
    )

    assert LegacyGateway is KnowledgeGateway
    assert LegacyDelegate is KnowledgeDelegate
