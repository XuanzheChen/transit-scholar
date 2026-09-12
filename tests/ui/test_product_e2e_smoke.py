"""Real end-to-end local product smoke (T-010).

This gate boots the real TransitScholar application composition on a loopback
origin that serves both ``/api/v1/*`` and the built frontend (``ui/dist``), then
drives the built bundle in a fresh jsdom session through the real HTTP API with a
real paper PDF:

    Schema-bound Workspace -> real PDF import -> Workspace membership ->
    real Layer2 evidence parse + retrieval index -> Conversation -> research
    prompt -> live AgentRun Timeline -> final answer -> collapsed/reopenable
    Timeline -> answer citation -> cited local PDF -> Workspace Schema Wiki

What is real
------------
Everything in the product path: the production composition
(``ApiRuntimeContext`` -> ``build_local_product`` -> ``RuntimeFactory``), HTTP
transport, the product facade, workspace/conversation persistence, the AgentRun
execution service, the Layer3 Agent runtime (semantic run coordinator, built-in
Role registry and policies, Role execution store, action validation/execution,
ledgers, trace projection), the semantic retrieval planner, the Layer2 evidence
parse of the imported PDF, the BM25 retrieval index, the citation projection, and
the configured non-fake LLM provider.

No deterministic seam is injected: the gate never constructs ``RuntimeFactory``
with ``coordinator``/``policies``/``knowledge_service``/``semantic_decider``/
``retrieval_planner_provider``/``synthesis`` overrides, never substitutes a role
policy, and never routes a query deterministically.
``test_ac016_gate_injects_no_deterministic_semantic_seams`` pins that statically
and ``_assert_real_composition`` verifies the services that actually executed the
smoke's research prompt.

Provider boundary
-----------------
The workstation's configured OpenAI-compatible provider
(``https://opencode.ai/zen/go/v1``) requires an ``x-opencode-session`` routing
header on every request. The gate therefore points the application's supported
``TRANSIT_SCHOLAR_LLM_BASE_URL`` knob at ``scripts/product_smoke_llm_bridge.py``,
which forwards each request to the configured provider unchanged (same body, same
response) and only adds that required routing header. The bridge records one JSONL
line per forwarded request, so the gate can prove which application-level calls
the configured real provider actually answered. The bridge cannot fabricate,
rewrite, or replace a model request or response.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

from transit_scholar.api.runtime.manager import LocalExecutionManager

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_DIST = UI_ROOT / "dist"
SMOKE_SCRIPT = UI_ROOT / "scripts" / "smoke-product-e2e-live.mjs"
BRIDGE_SCRIPT = REPO_ROOT / "scripts" / "product_smoke_llm_bridge.py"
PDF_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "metadata"
    / "causal_reinforcement_learning_train_scheduling.pdf"
)

#: UI interactions the smoke must exercise, in the order of the product flow.
PRODUCT_FLOW_MARKERS = (
    "new-workspace-button",
    "schema-binding-warning",
    "workspace-schema-immutable",
    "import-pdf-button",
    "import-paper-file-input",
    "open-primary-pdf",
    "add-paper-to-workspace",
    "new-conversation-button",
    "prompt-input",
    "research-primary-control",
    "active-run",
    "run-timeline",
    "turn-final-answer",
    "turn-timeline-",
    "answer-citations",
    "citation-reference-1",
    "citation-detail-dialog",
    "open-citation-pdf",
    "wiki-status",
    "wiki-source-schema",
    "wiki-source-agentic",
    "wiki-search-input",
)

#: Production service classes the AC-016 composition must contain.
PRODUCTION_COMPOSITION = (
    "RunCoordinatorRole",
    "SemanticRunCoordinationPolicy",
    "StructuredRunSemanticDecider",
    "KnowledgeToolService",
    "HybridKnowledgeRetrievalPlanner",
    "_RuntimeRetrievalPlannerProvider",
    "StructuredLLMRolePolicy",
    "RunFinalSynthesisRole",
)

#: Deterministic semantic substitutions that must never appear in this gate.
FORBIDDEN_SEAMS = (
    "_scripted_policies",
    "_DeterministicRetrievalPlanner",
    "_RealRetrievalKnowledge",
    "_NoopLifecycle",
    "OptionalPlanningPolicy",
    "build_fallback_run_coordinator",
    "RuntimeFactory(",
    "semantic_decider=",
    "coordinator=",
    "policies=",
    "knowledge_service=",
    "retrieval_planner_provider=",
    "l3s7_lifecycle=",
    "episodic_memory=",
    "run_config=",
)

#: Role prompt heads that identify one answered application-level Role call.
ROLE_PROMPT_PREFIXES = {
    "research_coordinator": "Observe session-level research progress.",
    "query_planning": "Propose or refine research queries",
    "evidence_reasoning": "Assess retrieved evidence for admission",
    "claim_reasoning": "Use accepted evidence to propose claims",
    "final_synthesis": "Synthesize the final user-facing answer",
}

#: ``tests/conftest.py``'s autouse offline guard replaces
#: ``socket.create_connection`` with a wrapper whose loopback branch calls the
#: module attribute it has just replaced, so any loopback HTTP client inside a
#: pytest process recurses. This gate must reach a loopback application, so it
#: restores the real function captured at import time (before fixtures run).
#: The guard's ``socket`` class patch stays in force, so the test process still
#: cannot dial anything but loopback; the provider bridge process owns egress.
_REAL_CREATE_CONNECTION = socket.create_connection


def _require_ac016_prerequisite(condition: bool, message: str) -> None:
    """Fail closed for the formal gate while retaining ordinary suite skips."""
    if condition:
        return
    if os.environ.get("TRANSIT_SCHOLAR_AC016_FORMAL", "").strip() == "1":
        pytest.fail(f"AC-016 prerequisite failure: {message}", pytrace=False)
    pytest.skip(message)


# --------------------------------------------------------------- static gate


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


def test_product_e2e_smoke_covers_the_real_product_flow():
    assert SMOKE_SCRIPT.is_file(), "the real product smoke harness is missing"
    body = SMOKE_SCRIPT.read_text(encoding="utf-8")
    for marker in PRODUCT_FLOW_MARKERS:
        assert marker in body, f"the product smoke does not exercise {marker}"
    for step in ("--prepare-request", "--prepare-ready", "--result-file", "--run-timeout-ms"):
        assert step in body, f"the product smoke does not support {step}"
    # The UI must never be bypassed: no Python/product invocation, no backend
    # process of its own, and no non-`/api/v1` transport for product records.
    for forbidden in ("child_process", "spawnSync", "execSync", "transit_scholar"):
        assert forbidden not in body, f"the product smoke references {forbidden}"
    assert "startsWith('/api/v1/')" in body
    assert "the UI performed non-API requests during the flow" in body


def test_product_e2e_smoke_is_wired_into_the_frontend_scripts():
    scripts = _package()["scripts"]
    assert "smoke:product-e2e" in scripts
    assert "smoke-product-e2e-live.mjs" in scripts["smoke:product-e2e"]


def test_formal_ac016_gate_is_fail_closed_and_has_one_entry_point():
    runner = REPO_ROOT / "scripts" / "run_ac016_acceptance.py"
    assert runner.is_file(), "the formal AC-016 runner is missing"
    body = runner.read_text(encoding="utf-8")
    assert 'TRANSIT_SCHOLAR_AC016_FORMAL' in body
    assert 'test_real_product_smoke_runs_end_to_end_through_the_built_ui' in body
    gate = Path(__file__).read_text(encoding="utf-8")
    assert "AC-016 prerequisite failure" in gate


def test_product_e2e_smoke_uses_a_real_pdf_and_real_library_import():
    body = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "papers/import" in body
    assert "import-paper-file-input" in body
    assert "causal_reinforcement_learning_train_scheduling.pdf" in body
    assert "open-citation-pdf" in body


def test_ac016_gate_injects_no_deterministic_semantic_seams():
    """AC-016: the passing gate must not substitute semantic model decisions."""
    body = Path(__file__).read_text(encoding="utf-8")
    # The declaration block below necessarily lists the seam names; scan
    # everything else in this gate module.
    declaration = (
        "FORBIDDEN_SEAMS = (\n"
        + "".join(f'    "{seam}",\n' for seam in FORBIDDEN_SEAMS)
        + ")\n"
    )
    assert declaration in body, "the seam declaration block was not found verbatim"
    scanned = body.replace(declaration, "", 1)
    for seam in FORBIDDEN_SEAMS:
        assert seam not in scanned, (
            f"the AC-016 gate still injects the deterministic seam {seam!r}; "
            "deterministic seams belong in separate unit tests only"
        )


def test_provider_routing_bridge_only_adds_the_routing_header():
    """The bridge may add the routing header, never rewrite a model exchange."""
    assert BRIDGE_SCRIPT.is_file(), "the provider routing bridge is missing"
    body = BRIDGE_SCRIPT.read_text(encoding="utf-8")
    assert "x-opencode-session" in body
    # It forwards the caller's body verbatim and returns the provider's own
    # status/headers/body; there is no response synthesis path.
    assert "content=body" in body
    assert "self.send_response(upstream.status_code)" in body
    assert "client.request(" in body
    for fabricated in ("canned", "FakeLLM", "stub_response"):
        assert fabricated not in body, f"the bridge fabricates model output: {fabricated}"


def test_provider_routing_bridge_has_one_authoritative_serve_loop():
    body = BRIDGE_SCRIPT.read_text(encoding="utf-8")
    main_body = body.split("def main(", 1)[1]
    assert "thread.join()" in main_body
    assert "server.serve_forever()" not in main_body
    assert "target=server.serve_forever" in body


# ------------------------------------------------------------------ fixtures


def _bridge_entries(log_path: Path) -> list[dict]:
    """Read the bridge's own record of every forwarded provider request."""
    if not log_path.is_file():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return [entry for entry in entries if entry.get("kind") != "body"]


@pytest.fixture
def product_root():
    """Short repository-local data root (keeps Windows artifact paths bounded).

    The process-global settings and default session factory are rebound to the
    isolated root as well, because the repository's Layer2 entry point resolves
    its Layer1 gate through them (exactly as the documented single-root local run
    does when ``TRANSIT_SCHOLAR_DATA_DIR`` is set before import). Every one of
    those process-global bindings is restored afterwards, so this gate cannot
    poison the rest of the suite with a deleted root.
    """
    root = (REPO_ROOT / "temp" / f"prod-e2e-{uuid4().hex[:8]}").resolve()
    root.mkdir(parents=True, exist_ok=True)
    from transit_scholar.config import settings as global_settings

    previous_data_dir = os.environ.get("TRANSIT_SCHOLAR_DATA_DIR")
    previous_root = global_settings.data_root
    from transit_scholar.db.engine import SessionLocal, engine_for

    previous_bind = SessionLocal.kw.get("bind")
    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(root)
    global_settings.data_root = root
    global_settings.init_directories()
    SessionLocal.configure(bind=engine_for(global_settings.database_url))
    try:
        yield root
    finally:
        if previous_data_dir is None:
            os.environ.pop("TRANSIT_SCHOLAR_DATA_DIR", None)
        else:
            os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = previous_data_dir
        if previous_root is not None:
            global_settings.data_root = previous_root
            global_settings.init_directories()
        if previous_bind is not None:
            SessionLocal.configure(bind=previous_bind)
        if os.environ.get("TRANSIT_SCHOLAR_KEEP_SMOKE_ROOT", "").strip() not in ("1", "true", "yes"):
            shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def configured_provider(monkeypatch):
    """Require the configured real provider and restore its boundary facts.

    ``tests/conftest.py`` deletes the ``TRANSIT_SCHOLAR_LLM_*`` variables so the
    deterministic offline suites never resolve a developer's real provider. This
    gate is the one test that needs them, so it re-reads the project ``.env``
    (the same file the local application loads at startup) and restores exactly
    those values; every other isolation the offline guard installs stays in
    force, and the application only ever talks to loopback.
    """
    from dotenv import dotenv_values

    dotenv = {
        name: value
        for name, value in dotenv_values(REPO_ROOT / ".env").items()
        if value
    }
    provider = dotenv.get("TRANSIT_SCHOLAR_LLM_PROVIDER", "")
    model = dotenv.get("TRANSIT_SCHOLAR_LLM_MODEL", "")
    base_url = dotenv.get("TRANSIT_SCHOLAR_LLM_BASE_URL", "")
    api_key = dotenv.get("TRANSIT_SCHOLAR_LLM_API_KEY", "")
    _require_ac016_prerequisite(
        provider == "openai_compatible" and bool(model and base_url and api_key),
        (
            "the configured real LLM provider is not available "
            "(TRANSIT_SCHOLAR_LLM_PROVIDER/MODEL/BASE_URL/API_KEY)"
        ),
    )
    for name, value in dotenv.items():
        if name.startswith("TRANSIT_SCHOLAR_LLM_"):
            monkeypatch.setenv(name, value)
    # The offline gate blocks real providers; the configured local application
    # runs with it disabled. Only this documented switch is changed.
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "0")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK", "1")
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
    }


def _bridge_port(process: "subprocess.Popen[str]") -> int:
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        line = process.stdout.readline() if process.stdout else ""
        if line.startswith("LLM_BRIDGE_PORT="):
            return int(line.split("=", 1)[1].strip())
        if process.poll() is not None:
            raise AssertionError(
                "the provider routing bridge exited before serving: "
                f"{process.stderr.read() if process.stderr else ''}"
            )
    raise AssertionError("the provider routing bridge never reported its port")


@pytest.fixture
def llm_bridge(configured_provider, product_root, monkeypatch):
    """Route the application at the configured provider through the bridge.

    The bridge runs as its own process: it is the only component that leaves
    loopback, so the offline socket guard installed for the deterministic suites
    keeps guarding this test process while the real provider is still reached.
    """
    log_path = product_root / "llm-bridge.jsonl"
    process = subprocess.Popen(
        [
            sys.executable,
            str(BRIDGE_SCRIPT),
            "--target",
            configured_provider["base_url"],
            "--local-base-path",
            "/v1",
            "--port",
            "0",
            "--log-file",
            str(log_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        port = _bridge_port(process)
        monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_BASE_URL", f"http://127.0.0.1:{port}/v1")
        yield log_path
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=20)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


# ------------------------------------------------------------------ helpers


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


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
        except (urllib.error.URLError, OSError, TimeoutError) as exc:  # pragma: no cover
            last = exc
        time.sleep(0.1)
    raise AssertionError(f"the integration server never became healthy: {last}")


def _fetch_text(url: str) -> str:
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.status == 200, f"GET {url} -> HTTP {response.status}"
        return response.read().decode("utf-8", errors="replace")


def _build_runtime_context(root: Path):
    """The real production composition, exactly as the local application starts.

    ``ApiRuntimeContext.initialize`` runs ``build_local_product``: real settings,
    the real database upgrade, real provider resolution, and a ``RuntimeFactory``
    with no injected coordinator, role policy, retrieval planner, or synthesis.
    """
    from transit_scholar.api.runtime_context import ApiRuntimeContext
    from transit_scholar.config import Settings

    settings = Settings(data_root=root)
    settings.init_directories()
    return ApiRuntimeContext(settings).initialize()


def _assert_real_composition(context, configured_provider, run_id: str) -> None:
    """AC-016: prove the run scope is the production composition, with no seam.

    ``run_id`` is the AgentRun the UI created through the real API, so this
    inspects the services that actually executed the smoke's research prompt.
    """
    factory = context.runtime_factory
    assert factory is not None, "the AC-016 composition has no runtime factory"
    for attribute in (
        "policies",
        "coordinator",
        "semantic_decider",
        "knowledge",
        "knowledge_service",
        "retrieval_planner_provider",
        "synthesis",
        "role_registry",
        "main_config",
        "run_config",
        "l3s7_lifecycle",
        "episodic_memory",
        "state_store",
        "role_store",
    ):
        assert getattr(factory, attribute) is None, (
            f"the AC-016 composition injected {attribute!r} instead of the "
            "production runtime"
        )
    client = factory.llm_client
    assert client is not None and client.is_fake is False, (
        "the AC-016 composition must use a non-fake LLM client"
    )
    assert client.provider_name == configured_provider["provider"]
    assert client.model_name == configured_provider["model"]
    assert client.config.base_url.startswith("http://127.0.0.1:"), (
        "the application was not pointed at the provider routing bridge"
    )

    scope = factory.build_run_scope(run_id)
    try:
        names = [
            type(scope.coordinator).__name__,
            type(scope.coordinator.policy).__name__,
            type(scope.coordinator.policy.semantic_decider).__name__,
            type(scope.knowledge).__name__,
            type(scope.knowledge.planner).__name__,
            type(scope.knowledge.planner.provider).__name__,
            type(scope.run_runtime.synthesis).__name__,
        ]
        names.extend(type(policy).__name__ for policy in scope.main_runtime.policies.values())
        for expected in PRODUCTION_COMPOSITION:
            assert expected in names, (
                "the AC-016 run scope does not compose the real "
                f"{expected}: {sorted(set(names))}"
            )
        # No deterministic run coordinator, role policy, retrieval planner,
        # reranker, or final-answer composer is injected anywhere.
        assert type(scope.coordinator).__name__ == "RunCoordinatorRole"
        assert type(scope.coordinator.policy).__name__ == "SemanticRunCoordinationPolicy"
        assert type(scope.coordinator.policy.semantic_decider).__name__ == (
            "StructuredRunSemanticDecider"
        )
        assert set(
            type(policy).__name__ for policy in scope.main_runtime.policies.values()
        ) == {"StructuredLLMRolePolicy"}
        assert type(scope.knowledge.planner).__name__ == "HybridKnowledgeRetrievalPlanner"
        assert type(scope.knowledge.planner.provider).__name__ == (
            "_RuntimeRetrievalPlannerProvider"
        )
        assert type(scope.run_runtime.synthesis).__name__ == "RunFinalSynthesisRole"
        assert scope.run_runtime.session_runtime is scope.main_runtime
    finally:
        scope.close()


def _prepare_evidence(root: Path, request_path: Path, ready_path: Path) -> None:
    """Build the real Layer2 evidence parse + retrieval index for one Paper."""
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.retrieval.api import build_retrieval

    deadline = time.monotonic() + 600.0
    while not request_path.exists():
        if time.monotonic() > deadline:
            raise AssertionError("the smoke never requested evidence preparation")
        time.sleep(0.2)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    paper_id = payload["paperId"]

    try:
        settings = Settings(data_root=root)
        settings.init_directories()
        config = Layer2Config.from_settings(settings)
        # The always-available local PyMuPDF adapter; no network or heavy models.
        object.__setattr__(config, "parser_override", "pymupdf_native")
        parsed = parse_paper(paper_id, config=config)
        built = build_retrieval(paper_id, config=config)
    except Exception as error:  # surface preparation failures immediately
        ready_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "paperId": paper_id,
                    "error": f"{type(error).__name__}: {error}",
                }
            ),
            encoding="utf-8",
        )
        return
    ready_path.write_text(
        json.dumps(
            {
                "status": "ready" if built.get("status") == "ok" else "failed",
                "paperId": paper_id,
                "parseStatus": str(getattr(parsed, "status", "")),
                "parseRunId": getattr(parsed, "parse_run_id", None),
                "retrievalStatus": built.get("status"),
            }
        ),
        encoding="utf-8",
    )


def _diagnostics(context, log_path: Path, prepare_ready: Path, manager, result_path: Path) -> str:
    """Dump the machine truth the harness cannot see when the flow fails."""
    import traceback

    from sqlalchemy import select

    from transit_scholar.db.models import AgentRun, AgentTraceEvent

    lines = ["--- diagnostics ---"]
    if result_path.is_file():
        try:
            flow = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            flow = {}
        lines.append(f"harness failure: {flow.get('failure')!r}")
        lines.append(f"harness API requests ({len(flow.get('apiRequests') or [])}):")
        for request in flow.get("apiRequests") or []:
            lines.append(f"  {request}")
    else:
        lines.append("harness wrote no result file")
    ready = prepare_ready.read_text(encoding="utf-8") if prepare_ready.is_file() else "<none>"
    lines.append(f"evidence preparation: {ready}")
    entries = _bridge_entries(log_path)
    lines.append(f"provider bridge forwarded {len(entries)} request(s)")
    for entry in entries:
        lines.append(
            "  bridge "
            f"status={entry.get('status')} format={entry.get('response_format')} "
            f"prompt={(entry.get('prompt_head') or '')[:70]!r}"
        )
    for failure in getattr(manager, "failures", []):
        lines.append("execution worker raised:")
        lines.append(
            "".join(
                traceback.format_exception(type(failure), failure, failure.__traceback__)
            )
        )
    try:
        with context.session_factory() as session:
            runs = session.scalars(
                select(AgentRun).order_by(AgentRun.created_at.desc()).limit(3)
            ).all()
            for run in runs:
                events = session.scalars(
                    select(AgentTraceEvent.event_type)
                    .where(AgentTraceEvent.agent_run_id == run.id)
                    .order_by(AgentTraceEvent.sequence)
                ).all()
                lines.append(
                    f"  run {run.id} status={run.status} events={len(events)} "
                    f"types={sorted(set(events))}"
                )
    except Exception as error:  # diagnostics must never mask the real failure
        lines.append(f"  run diagnostics unavailable: {type(error).__name__}: {error}")
    return "\n".join(lines)


# ---------------------------------------------------------------- live gate


@pytest.fixture
def ac016_prerequisites():
    forced_missing = os.environ.get("TRANSIT_SCHOLAR_AC016_FORCE_MISSING", "").strip()
    _require_ac016_prerequisite(
        not forced_missing,
        f"deliberately unavailable prerequisite: {forced_missing}",
    )
    node = shutil.which("node")
    _require_ac016_prerequisite(node is not None, "Node.js is required to drive the built frontend")
    _require_ac016_prerequisite(
        (UI_DIST / "index.html").is_file(),
        "run `npm run build` in ui/ before the real product smoke",
    )
    _require_ac016_prerequisite(PDF_FIXTURE.is_file(), f"missing PDF fixture {PDF_FIXTURE}")
    return node


class _RecordingExecutionManager(LocalExecutionManager):
    """The production execution manager that keeps worker exceptions.

    This is a diagnostic observer only: it records the exception the production
    worker would otherwise lose inside its discarded ``Future``, then re-raises
    it unchanged so execution behaviour is identical.
    """

    def __init__(self, product_factory) -> None:
        super().__init__(product_factory)
        self.failures: list[BaseException] = []

    def _run(self, agent_run_id: str, resume: bool = False):  # noqa: D102 - see base
        try:
            return super()._run(agent_run_id, resume)
        except BaseException as error:  # noqa: BLE001 - recorded then re-raised
            self.failures.append(error)
            raise


def test_real_product_smoke_runs_end_to_end_through_the_built_ui(
    ac016_prerequisites, product_root, llm_bridge, configured_provider
):
    node = ac016_prerequisites

    import uvicorn

    from sqlalchemy import select

    from transit_scholar.api import create_app
    from transit_scholar.db.models import AgentRun, AgentTraceEvent, EvidenceRecord

    prepare_request = product_root / "prepare.request.json"
    prepare_ready = product_root / "prepare.ready.json"
    result_path = product_root / "flow-result.json"
    context = _build_runtime_context(product_root)
    assert context.agent_runtime_available, (
        "the configured real provider did not resolve an agent runtime; the "
        "running local application would report PROVIDER_UNAVAILABLE"
    )
    # The production manager, with one test-side observer: the worker exception
    # is otherwise swallowed by the future, which makes a failed run
    # undiagnosable. The observer never alters execution.
    manager = _RecordingExecutionManager(context.create_product)
    app = create_app(runtime_context=context, execution_manager=manager)
    assert Path(str(app.state.ui_dist)) == UI_DIST, "the API did not resolve the built frontend"

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, name="product-e2e-server", daemon=True)
    thread.start()
    preparer = threading.Thread(
        target=_prepare_evidence,
        args=(product_root, prepare_request, prepare_ready),
        name="product-e2e-preparer",
        daemon=True,
    )
    preparer.start()

    try:
        _wait_for_health(base_url)
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
                "--prepare-request",
                str(prepare_request),
                "--prepare-ready",
                str(prepare_ready),
                "--result-file",
                str(result_path),
                "--run-timeout-ms",
                "1800000",
            ],
            cwd=str(UI_ROOT),
            capture_output=True,
            text=True,
            timeout=2400,
            env={**os.environ, "TRANSIT_SCHOLAR_SMOKE_API_BASE": base_url},
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode != 0:
            raise AssertionError(
                "real product smoke failed:\n"
                f"{output}\n"
                f"{_diagnostics(context, llm_bridge, prepare_ready, manager, result_path)}"
            )
        assert "PASS: real end-to-end local product smoke completed" in completed.stdout

        flow = json.loads(result_path.read_text(encoding="utf-8"))
        assert flow["workspaceId"] and flow["paperId"] and flow["conversationId"] and flow["runId"]
        assert flow["timelineEventCountActive"] >= 1
        assert flow["timelineEventCountAfterReopen"] >= 1
        assert flow["finalAnswer"], "the UI displayed no final answer"
        assert flow["citationEvidenceId"], "the UI exposed no answer citation"
        assert flow["citationPaperId"] == flow["paperId"]
        assert flow["citationPages"], "the answer citation carries no page locator"
        assert flow["citationHref"] and "/api/v1/files/" in flow["citationHref"]
        assert flow["wikiStatus"], "the Wiki status could not be read"
        # A never-built Base Wiki is a real backend state: the smoke accepts the
        # backend's WIKI_MISSING answer only when the UI surfaces it explicitly,
        # and never a fabricated hit list.
        assert flow["wikiSearchOutcome"] in ("results", "WIKI_MISSING")
        assert all(request.split(" ", 1)[1].startswith("/api/v1/") for request in flow["apiRequests"])
        for required in ("papers/import", "/timeline", "/conversations", "/wiki"):
            assert any(required in request for request in flow["apiRequests"]), required

        # ------------------------------------------- real composition (AC-016)
        _assert_real_composition(context, configured_provider, flow["runId"])

        # ------------------------------------------------ provider evidence
        entries = _bridge_entries(llm_bridge)
        assert entries, (
            "the configured provider routing bridge recorded no forwarded "
            "request: the run never reached the real provider"
        )
        answered = [entry for entry in entries if entry.get("status") == 200]
        assert len(answered) >= 5, (
            "the configured real provider answered too few forwarded calls for a "
            f"completed AgentRun: {[entry.get('status') for entry in entries]}"
        )
        assert all(entry.get("model") == configured_provider["model"] for entry in answered), (
            "the provider routing bridge forwarded a different model than configured"
        )
        transport_failures = [
            entry for entry in entries if entry.get("status") in (500, 502, 503, 504)
        ]
        assert not transport_failures, (
            f"the bridge could not reach the configured provider: {transport_failures}"
        )

        with context.session_factory() as session:
            run = session.get(AgentRun, flow["runId"])
            assert run is not None and run.status == "completed", (
                "the real configured model did not complete the AgentRun: "
                f"{getattr(run, 'status', None)}"
            )
            events = session.scalars(
                select(AgentTraceEvent).where(AgentTraceEvent.agent_run_id == flow["runId"])
            ).all()
            assert events, "the AgentRun projected no Trace events"
            role_ids = set()
            for event in events:
                if not event.event_type.startswith("role."):
                    continue
                payload = json.loads(event.payload_json or "{}")
                role_id = payload.get("role_id")
                if role_id:
                    role_ids.add(role_id)
            for role_id in ROLE_PROMPT_PREFIXES:
                assert role_id in role_ids, (
                    f"the completed AgentRun never executed the {role_id} Role"
                )

            # The configured provider answered every application-level Role call
            # the completed AgentRun used: correlate the real Role prompt
            # templates with the bridge's record of real provider responses.
            recorded_prompts = [str(entry.get("prompt_head") or "") for entry in answered]
            for role_id, prefix in ROLE_PROMPT_PREFIXES.items():
                assert any(prompt.startswith(prefix) for prompt in recorded_prompts), (
                    f"the configured real provider never answered the {role_id} "
                    "Role call used by the completed AgentRun"
                )

            evidence = session.get(EvidenceRecord, flow["citationEvidenceId"])
            assert evidence is not None, "the cited evidence is not persisted under the run"
            locator = json.loads(evidence.locator_json)
            assert locator["paper_id"] == flow["paperId"]
            assert locator["pages"], "the persisted evidence has no page locator"
            assert evidence.text_snapshot.strip(), "the persisted evidence has no excerpt"

        # The displayed final answer is the model's own answer text, not text
        # composed by the gate: the persisted completed final-synthesis Role
        # execution holds the model output the run artifact carries.
        role_records = sorted(
            (product_root / "layer3" / "runs" / flow["runId"] / "roles").glob("*.json")
        )
        assert role_records, "the run persisted no Role execution records"
        model_answers = []
        for record in role_records:
            payload = json.loads(record.read_text(encoding="utf-8"))
            if payload.get("role_id") != "final_synthesis":
                continue
            if payload.get("status") != "completed":
                continue
            last_output = payload.get("working_state", {}).get("last_output") or {}
            answer = last_output.get("answer_text")
            if isinstance(answer, str) and answer.strip():
                model_answers.append(answer.strip())
        assert model_answers, (
            "no completed final-synthesis Role execution persisted a model-authored answer"
        )
        assert any(answer in flow["finalAnswer"] for answer in model_answers), (
            "the displayed final answer is not the text the real model authored"
        )
    finally:
        prepare_ready.write_text('{"status": "aborted"}', encoding="utf-8")
        server.should_exit = True
        thread.join(timeout=60)
        preparer.join(timeout=10)
