"""UI-S1 core flow integration gate (T-005).

This gate verifies that the first Web UI iteration is independently usable for
the complete primary research workflow *before* advanced UI features land:

    create/open Workspace -> import PDF -> add Paper to Workspace ->
    create Conversation -> submit prompt -> observe the live Timeline ->
    pause/resume -> final answer -> inspect citation -> open the local PDF ->
    open the workspace Wiki

Method
------
The gate boots the real product API with the real product/run composition on a
loopback origin that also serves the built frontend (``ui/dist``), then drives
the built bundle in a fresh jsdom session through the real HTTP API.

No LLM provider is reachable in the offline test environment, so the only
substituted seams are the LLM-facing policy/knowledge functions (the same
scripted seams the API freeze integration gate uses). Everything else — HTTP,
product facade, run orchestration, pause/resume, trace projection, citation
projection, PDF registration, and file-content serving — is the real code path.

The UI flow itself performs *no* Product/Core Python call: the bundled frontend
talks only to ``/api/v1/*``, and the harness fails the run if the UI ever
issues a request outside that prefix.

The heavy live test is skipped only when its prerequisites are genuinely
missing (no frontend build, no Node.js, no PDF fixture).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_DIST = UI_ROOT / "dist"
SMOKE_SCRIPT = UI_ROOT / "scripts" / "smoke-core-flow-live.mjs"
PDF_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "metadata"
    / "causal_reinforcement_learning_train_scheduling.pdf"
)

#: UI interactions the gate must exercise, in the order of the primary flow.
CORE_FLOW_MARKERS = (
    "new-workspace-button",
    "schema-binding-warning",
    "workspace-schema-immutable",
    "import-pdf-button",
    "import-paper-file-input",
    "paper-readiness",
    "add-paper-to-workspace",
    "add-workspace-paper-select",
    "new-conversation-button",
    "prompt-input",
    "research-primary-control",
    "run-timeline",
    "turn-final-answer",
    "conversation-turns",
    "answer-citations",
    "citation-reference-1",
    "open-citation-pdf",
    "wiki-status",
    "wiki-schema-unavailable",
)


# --------------------------------------------------------------- static gate


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


def test_core_flow_smoke_harness_covers_the_primary_research_path():
    assert SMOKE_SCRIPT.is_file(), "the core flow smoke harness is missing"
    body = SMOKE_SCRIPT.read_text(encoding="utf-8")
    for marker in CORE_FLOW_MARKERS:
        assert marker in body, f"the core flow smoke does not exercise {marker}"
    for step in ("--release-file", "--result-file"):
        assert step in body, f"the core flow smoke does not support {step}"


def test_core_flow_smoke_is_wired_into_the_frontend_scripts():
    scripts = _package()["scripts"]
    assert "smoke:core-flow" in scripts
    assert "smoke-core-flow-live.mjs" in scripts["smoke:core-flow"]


def test_core_flow_smoke_refuses_any_non_api_transport():
    body = SMOKE_SCRIPT.read_text(encoding="utf-8")
    # The UI may only speak the frozen product API during the flow.
    assert "nonApiRequests" in body
    assert "startsWith('/api/v1/')" in body
    assert "the UI performed non-API requests during the flow" in body
    # Direct Product/Core invocation would bypass HTTP entirely, so the harness
    # must not shell out to the product layer or start a backend of its own.
    for forbidden in ("child_process", "spawnSync", "execSync", "transit_scholar"):
        assert forbidden not in body, f"the smoke harness references {forbidden}"


# ------------------------------------------------------------- live gate


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def loopback_connect(monkeypatch):
    """Restore a working loopback connector for the gate's local server.

    The repository-wide offline guard replaces ``socket.create_connection`` with
    a loopback-only wrapper whose fallback accidentally calls the patched
    function again. The gate only ever talks to ``127.0.0.1``, so it installs a
    direct loopback connector for the duration of the test; non-loopback
    addresses stay blocked, preserving the guard's intent.
    """
    import socket as socket_module

    def _connect(address, timeout=socket_module._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError("network blocked by TRANSIT_SCHOLAR_BLOCK_NETWORK")
        sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        if timeout is not socket_module._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        sock.connect(address)
        return sock

    monkeypatch.setattr(socket_module, "create_connection", _connect)
    yield


def _wait_for_health(base_url: str, timeout: float = 90.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:  # pragma: no cover - retry path
            last = exc
        time.sleep(0.1)
    raise AssertionError(f"the integration server never became healthy: {last}")


def _fetch_text(url: str) -> str:
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.status == 200, f"GET {url} -> HTTP {response.status}"
        return response.read().decode("utf-8", errors="replace")


class _NoopLifecycle:
    """Run-end memory lifecycle stand-in (no LLM-backed distillation offline)."""

    episodic_store = object()

    def configure_authoritative_readers(self, **_kwargs) -> None:
        pass

    def maintain_before_session(self, *_args, **_kwargs) -> None:
        pass

    def complete_agent_run(self, **_kwargs) -> None:
        pass


class _ScriptedKnowledge:
    """Retrieval seam that returns evidence for a real workspace member paper.

    The evidence locator points at the Paper the UI actually imported, so the
    resulting answer citation resolves to a real Paper row and its registered
    local PDF file. The first call is held until the smoke has requested pause,
    which makes the pause/resume window deterministic instead of timing-based.
    """

    def __init__(self, session_factory, release_path: Path, hold_timeout: float = 120.0) -> None:
        self._session_factory = session_factory
        self._release_path = release_path
        self._hold_timeout = hold_timeout
        self._held = False
        self.paper_ids: list[str] = []

    def _first_member_paper_id(self, workspace_id: str) -> str | None:
        from transit_scholar.db.models import Paper, WorkspacePaperMembership

        with self._session_factory() as session:
            return session.scalar(
                select(Paper.id)
                .join(WorkspacePaperMembership, WorkspacePaperMembership.paper_id == Paper.id)
                .where(WorkspacePaperMembership.workspace_id == workspace_id)
            )

    def retrieve_knowledge(self, query):
        from transit_scholar.layer3.evidence import (
            EvidenceLocator,
            QueryProvenance,
            ResearchEvidence,
        )
        from transit_scholar.layer3.tools import RetrievalResultEnvelope

        paper_id = self._first_member_paper_id(query.workspace_id)
        if paper_id is None:
            raise AssertionError("the scripted retrieval ran without a workspace member paper")
        if paper_id not in self.paper_ids:
            self.paper_ids.append(paper_id)

        if not self._held:
            self._held = True
            deadline = time.monotonic() + self._hold_timeout
            while not self._release_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)

        return RetrievalResultEnvelope(
            query=query,
            evidence_results=[
                ResearchEvidence(
                    evidence_id="gate-evidence",
                    text="Transit signal priority reduces intersection delay.",
                    source_kind="paper",
                    locator=EvidenceLocator(
                        workspace_id=query.workspace_id,
                        source_kind="paper",
                        paper_id=paper_id,
                        pages=[2],
                        parse_run_id="gate-parse",
                        canonical_source_version="gate-parse",
                    ),
                    query_provenance=QueryProvenance(
                        query_id=query.query_id, session_id=query.session_id
                    ),
                )
            ],
        )


def _scripted_policies():
    """Deterministic policy seam mirroring the API freeze integration gate.

    ``progress`` is shared by every role policy, exactly as in the freeze gate:
    the research coordinator chooses the next role from what the evidence and
    claim steps have already completed. The scripted knowledge seam returns one
    admitted evidence item, so the final synthesis cites persisted evidence.
    """
    from transit_scholar.layer3.agent import RoleId

    progress: set[str] = set()

    class _Policy:
        def decide(self, definition, role_input, state, role_context, repair_context=None):
            if definition.role_id == RoleId.QUERY_PLANNING:
                progress.add("planning")
                return {"completed": True, "proposed_queries": ["transit intervention delay"]}
            if definition.role_id == RoleId.RESEARCH_COORDINATOR:
                if "planning" not in progress:
                    next_role = "query_planning"
                elif "evidence" not in progress:
                    next_role = "evidence_reasoning"
                elif "claim" not in progress:
                    next_role = "claim_reasoning"
                else:
                    next_role = "final_synthesis"
                return {"completed": True, "next_role_id": next_role}
            if definition.role_id == RoleId.EVIDENCE_REASONING:
                progress.add("evidence")
                return {"completed": True, "admitted_evidence_ids": ["gate-evidence"]}
            if definition.role_id == RoleId.CLAIM_REASONING:
                progress.add("claim")
                return {
                    "completed": True,
                    "proposed_claims": [
                        {
                            "statement": "Transit intervention reduces delay.",
                            "evidence_ids": list(role_input.accepted_evidence_ids),
                        }
                    ],
                }
            return {
                "completed": True,
                "answer_text": "Transit signal priority reduces intersection delay.",
                "citation_references": [item.evidence_id for item in role_input.accepted_evidence],
            }

    return {role: _Policy() for role in RoleId}


def _build_runtime_context(root: Path, release_path: Path):
    from transit_scholar.api.runtime_context import ApiRuntimeContext
    from transit_scholar.config import Settings
    from transit_scholar.db.base import Base
    from transit_scholar.layer2.schema_catalog import SchemaCatalog
    from transit_scholar.layer3.planning import RunDecision
    from transit_scholar.layer3.run_context import RunRuntimeConfig
    from transit_scholar.product.runtime import RuntimeFactory

    settings = Settings(data_root=root)
    settings.init_directories()
    engine = create_engine(
        f"sqlite:///{root / 'gate.sqlite'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    def coordinate(snapshot):
        if snapshot.session_outcomes:
            return RunDecision(mode="complete", completion_reason="done")
        return RunDecision(mode="direct_session", proposed_questions=["transit intervention delay"])

    context = ApiRuntimeContext(settings)
    context.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    context.schema_catalog = SchemaCatalog(root)
    context.runtime_factory = RuntimeFactory(
        session_factory=context.session_factory,
        data_root=root,
        l3s7_lifecycle=_NoopLifecycle(),
        episodic_memory=SimpleNamespace(retrieve=lambda **_kwargs: ()),
        run_config=RunRuntimeConfig(max_episodic_memory_candidates=0),
        policies=_scripted_policies(),
        knowledge_service=_ScriptedKnowledge(context.session_factory, release_path),
        coordinator=coordinate,
    )
    return context, engine


@pytest.fixture
def gate_root():
    """Short repository-local data root (keeps Windows artifact paths bounded)."""
    root = (Path("temp") / f"uigate-{uuid4().hex[:8]}").resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ui_s1_core_flow_runs_end_to_end_through_the_built_ui(gate_root, loopback_connect):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to drive the built frontend")
    if not (UI_DIST / "index.html").is_file():
        pytest.skip("run `npm run build` in ui/ before the live core flow gate")
    if not PDF_FIXTURE.is_file():
        pytest.skip(f"missing PDF fixture {PDF_FIXTURE}")

    import uvicorn

    from transit_scholar.api import create_app
    from transit_scholar.db.models import EvidenceRecord

    release_path = gate_root / "pause.release"
    result_path = gate_root / "flow-result.json"
    context, engine = _build_runtime_context(gate_root, release_path)
    # Same production composition the documented single-origin run uses: the
    # app resolves and serves the repository's built frontend itself.
    app = create_app(runtime_context=context)
    assert Path(str(app.state.ui_dist)) == UI_DIST, "the API did not resolve the built frontend"

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, name="ui-s1-gate-server", daemon=True)
    thread.start()

    try:
        _wait_for_health(base_url)
        # One origin serves both the product API and the built frontend.
        index_html = _fetch_text(f"{base_url}/")
        assert 'id="root"' in index_html, "the built frontend entry document was not served"
        completed = subprocess.run(
            [
                node,
                str(SMOKE_SCRIPT),
                "--base",
                base_url,
                "--dist",
                str(UI_DIST),
                "--pdf",
                str(PDF_FIXTURE),
                "--release-file",
                str(release_path),
                "--result-file",
                str(result_path),
            ],
            cwd=str(UI_ROOT),
            capture_output=True,
            text=True,
            timeout=900,
            env={**os.environ, "TRANSIT_SCHOLAR_SMOKE_API_BASE": base_url},
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        assert completed.returncode == 0, f"core flow smoke failed:\n{output}"
        assert "PASS: UI-S1 core research flow smoke completed" in completed.stdout

        flow = json.loads(result_path.read_text(encoding="utf-8"))
        assert flow["coreWorkspaceId"] and flow["schemaWorkspaceId"]
        assert flow["paperId"] and flow["conversationId"] and flow["runId"]
        assert flow["citationEvidenceId"]
        assert flow["apiRequests"], "the UI issued no API requests at all"
        assert all(request.split(" ", 1)[1].startswith("/api/v1/") for request in flow["apiRequests"])
        for required in ("papers/import", "/pause", "/resume", "/timeline"):
            assert any(required in request for request in flow["apiRequests"]), required

        # Independent repository evidence: the answer citation must resolve to
        # the real imported Paper, not to a frontend-invented reference.
        with context.session_factory() as session:
            evidence = session.get(EvidenceRecord, flow["citationEvidenceId"])
        assert evidence is not None, "the cited evidence is not persisted under the run"
        locator = json.loads(evidence.locator_json)
        assert locator["paper_id"] == flow["paperId"]
        assert locator["pages"] == [2]
    finally:
        release_path.write_text("release", encoding="utf-8")
        server.should_exit = True
        thread.join(timeout=60)
        engine.dispose()
