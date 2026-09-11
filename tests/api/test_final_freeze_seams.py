"""Final bounded API v1 freeze regressions for the three remaining seams."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from transit_scholar.api import create_app
from transit_scholar.db.models import (
    AgentRun,
    ConversationTurn,
    Paper,
    Workspace,
    WorkspacePaperMembership,
)
from transit_scholar.layer2.retrieval.providers import UnavailableError
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    LLMRequestError,
    LLMUnavailableError,
    get_schema_definition,
)
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunFinalResponseArtifact
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer3.workspace.errors import WorkspaceError
from transit_scholar.layer3.workspace.service import WorkspaceService
from transit_scholar.product import PaperInUseError
from transit_scholar.product import facade as facade_module
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.runtime import FileRunResearchStateStore
from test_freeze_runtime_integration import Lifecycle, context_for, freeze_root


ANSWER_A = "Exact answer A"


def test_l3s7_failure_cannot_overturn_durable_completed_intent(freeze_root):
    calls = {"coordinator": 0, "synthesis": 0, "lifecycle": 0}
    lifecycle_entered = Event()
    release_lifecycle = Event()

    class FailingLifecycle(Lifecycle):
        def complete_agent_run(self, **_kwargs):
            calls["lifecycle"] += 1
            lifecycle_entered.set()
            assert release_lifecycle.wait(20), "test did not release L3S7"
            raise RuntimeError("private lifecycle provider detail")

    def coordinator(_snapshot):
        calls["coordinator"] += 1
        return RunDecision(mode="complete", completion_reason="done")

    def synthesis(_snapshot):
        calls["synthesis"] += 1
        return RunFinalResponseArtifact(answer_text=ANSWER_A)

    context = context_for(
        freeze_root,
        coordinator=coordinator,
        synthesis=synthesis,
    )
    context.runtime_factory.l3s7_lifecycle = FailingLifecycle()
    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            workspace_id = client.post(
                "/api/v1/workspaces", json={"name": "Terminal truth"}
            ).json()["workspace_id"]
            conversation_id = client.post(
                f"/api/v1/workspaces/{workspace_id}/conversations",
                json={"title": "Terminal truth"},
            ).json()["conversation_id"]
            admitted = client.post(
                f"/api/v1/conversations/{conversation_id}/turns",
                json={"message": "Return answer A"},
            )
            assert admitted.status_code == 202, admitted.text
            run_id = admitted.json()["agent_run_id"]

            assert lifecycle_entered.wait(20), "L3S7 was not attempted"
            future = app.state.execution_manager.future(run_id)
            assert future is not None

            # Authoritative completion is committed before auxiliary L3S7.
            with context.session_factory() as fresh:
                assert fresh.get(AgentRun, run_id).status == "completed"
                events = AgentTraceService(fresh).read_trace(agent_run_id=run_id)
                assert [event.event_type for event in events].count("run.completed") == 1
                assert [event.event_type for event in events].count("run.failed") == 0

            release_lifecycle.set()
            result = future.result(timeout=20)
            assert result["status"] == "completed"
            assert result["final_response"].answer_text == ANSWER_A

            checkpoint = FileRunResearchStateStore(
                context.runtime_factory.runtime_root
            ).load(run_id)
            assert checkpoint["orchestration_state"]["status"] == "completed"
            assert checkpoint["final_response"]["answer_text"] == ANSWER_A

            with context.session_factory() as fresh:
                assert fresh.get(AgentRun, run_id).status == "completed"
                turn = fresh.scalar(
                    select(ConversationTurn).where(
                        ConversationTurn.agent_run_id == run_id
                    )
                )
                assert turn.status == "completed"
                assert turn.final_assistant_response["answer_text"] == ANSWER_A
                event_types = [
                    event.event_type
                    for event in AgentTraceService(fresh).read_trace(
                        agent_run_id=run_id
                    )
                ]
                assert event_types.count("run.completed") == 1
                assert event_types.count("run.failed") == 0
                assert event_types.count("run.lifecycle.warning") == 1

            timeline = client.get(f"/api/v1/runs/{run_id}/timeline")
            assert timeline.status_code == 200
            assert "private lifecycle provider detail" not in timeline.text
            assert any(
                event["kind"] == "warning"
                and event["data"]["code"] == "RUN_WARNING"
                for event in timeline.json()["events"]
            )
            assert calls == {"coordinator": 1, "synthesis": 1, "lifecycle": 1}
    finally:
        release_lifecycle.set()
        app.state.execution_manager.shutdown()
        context.session_factory.kw["bind"].dispose()


def test_deleted_paper_cannot_be_added_to_active_workspace(freeze_root):
    context = context_for(freeze_root)
    seed = context.create_product()
    workspace = seed.create_workspace("Deleted paper rejection")
    seed.session.add(Paper(id="deleted-paper", title="Deleted", status="active"))
    seed.session.commit()
    seed.close()
    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            deleted = client.delete("/api/v1/papers/deleted-paper")
            assert deleted.status_code == 200, deleted.text
            added = client.post(
                f"/api/v1/workspaces/{workspace.workspace_id}/papers",
                json={"paper_id": "deleted-paper"},
            )
            assert added.status_code == 409, added.text
            assert added.json()["error"]["code"] == "INVALID_STATE"
            assert "deleted" not in added.json()["error"]["message"].lower()

        with context.session_factory() as fresh:
            assert fresh.get(Paper, "deleted-paper").status == "deleted"
            memberships = fresh.scalars(
                select(WorkspacePaperMembership).where(
                    WorkspacePaperMembership.paper_id == "deleted-paper"
                )
            ).all()
            assert memberships == []
    finally:
        context.session_factory.kw["bind"].dispose()


def test_active_workspace_member_still_blocks_global_paper_delete(freeze_root):
    context = context_for(freeze_root)
    seed = context.create_product()
    workspace = seed.create_workspace("Existing reverse rule")
    seed.session.add(Paper(id="member-paper", title="Member", status="active"))
    seed.session.commit()
    seed.add_workspace_paper(workspace.workspace_id, "member-paper")
    seed.close()
    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            deleted = client.delete("/api/v1/papers/member-paper")
            assert deleted.status_code == 409, deleted.text
            assert deleted.json()["error"]["code"] == "PAPER_IN_USE"

        with context.session_factory() as fresh:
            assert fresh.get(Paper, "member-paper").status == "active"
            assert fresh.scalar(
                select(WorkspacePaperMembership).where(
                    WorkspacePaperMembership.workspace_id == workspace.workspace_id,
                    WorkspacePaperMembership.paper_id == "member-paper",
                )
            ) is not None
    finally:
        context.session_factory.kw["bind"].dispose()


class _ObservedMutationLock:
    """Expose a deterministic signal when a second mutation reaches the lock."""

    def __init__(self):
        self._lock = Lock()
        self._counter_lock = Lock()
        self._attempts = 0
        self.second_attempt = Event()

    def __enter__(self):
        with self._counter_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempt.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._lock.release()


def _mutation_product(context, mutation_lock):
    return TransitScholarProduct(
        context.session_factory(),
        runtime_factory=None,
        data_root=context.settings.data_root,
        schema_catalog=context.schema_catalog,
        settings_obj=context.settings,
        session_factory=context.session_factory,
        paper_membership_mutation_lock=mutation_lock,
    )


@pytest.mark.parametrize("first_operation", ["delete", "add"])
def test_concurrent_delete_and_membership_add_preserve_invariant(
    freeze_root, monkeypatch, first_operation
):
    context = context_for(freeze_root)
    seed = context.create_product()
    workspace = seed.create_workspace(f"Concurrent {first_operation}")
    paper_id = f"race-{first_operation}"
    seed.session.add(Paper(id=paper_id, title="Race", status="active"))
    seed.session.commit()
    seed.close()

    entered = Event()
    release = Event()
    mutation_lock = _ObservedMutationLock()

    def add():
        product = _mutation_product(context, mutation_lock)
        try:
            return product.add_workspace_paper(workspace.workspace_id, paper_id)
        finally:
            product.close()

    def delete():
        product = _mutation_product(context, mutation_lock)
        try:
            return product.soft_delete_library_paper(paper_id)
        finally:
            product.close()

    if first_operation == "delete":
        original_delete = facade_module.soft_delete_paper

        def blocked_delete(*args, **kwargs):
            entered.set()
            assert release.wait(20), "test did not release delete"
            return original_delete(*args, **kwargs)

        monkeypatch.setattr(facade_module, "soft_delete_paper", blocked_delete)
        first_call, second_call = delete, add
    else:
        original_add = WorkspaceService.add_paper

        def blocked_add(self, *args, **kwargs):
            entered.set()
            assert release.wait(20), "test did not release membership add"
            return original_add(self, *args, **kwargs)

        monkeypatch.setattr(WorkspaceService, "add_paper", blocked_add)
        first_call, second_call = add, delete

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_call)
            assert entered.wait(20), f"{first_operation} did not enter boundary"
            second = pool.submit(second_call)
            assert mutation_lock.second_attempt.wait(20), "second mutation did not reach boundary"
            assert not second.done()
            release.set()
            if first_operation == "delete":
                assert first.result(timeout=20).status == "deleted"
                with pytest.raises(WorkspaceError) as rejected:
                    second.result(timeout=20)
                assert rejected.value.code == "invalid_state"
            else:
                assert first.result(timeout=20).membership.paper_id == paper_id
                with pytest.raises(PaperInUseError):
                    second.result(timeout=20)
    finally:
        release.set()

    with context.session_factory() as fresh:
        paper = fresh.get(Paper, paper_id)
        active_membership = fresh.scalar(
            select(WorkspacePaperMembership)
            .join(Workspace, Workspace.id == WorkspacePaperMembership.workspace_id)
            .where(
                WorkspacePaperMembership.paper_id == paper_id,
                Workspace.status == "active",
            )
        )
        assert not (paper.status == "deleted" and active_membership is not None)
        if first_operation == "delete":
            assert paper.status == "deleted"
            assert active_membership is None
        else:
            assert paper.status == "active"
            assert active_membership is not None
    context.session_factory.kw["bind"].dispose()


def _seed_bound_schema(context, *, paper_id: str):
    definition = get_schema_definition("bus_control_rl")
    product = context.create_product()
    workspace = product.create_workspace(
        f"Provider boundary {paper_id}",
        definition.schema_id,
        definition.version,
    )
    product.session.add(Paper(id=paper_id, title="Provider paper", status="active"))
    product.session.commit()
    product.add_workspace_paper(workspace.workspace_id, paper_id)
    materialized = product.materialize_workspace_schema(
        workspace.workspace_id,
        paper_id,
        llm_client=FakeLLMProvider(),
    )
    product.close()
    return workspace.workspace_id, materialized.run_id


@pytest.mark.parametrize(
    "provider_error",
    [
        LLMUnavailableError("private schema provider URL"),
        LLMRequestError("private schema timeout", status_code=503),
    ],
    ids=["unavailable", "transport"],
)
def test_schema_provider_unavailable_is_503_and_preserves_current(
    freeze_root, monkeypatch, provider_error
):
    context = context_for(freeze_root)
    paper_id = f"schema-{provider_error.error_code}"
    workspace_id, stable_run_id = _seed_bound_schema(context, paper_id=paper_id)
    storage = workspace_layout(workspace_id, data_root=freeze_root).schema_storage()
    pointer_before = storage.current_path(paper_id).read_bytes()
    runs_before = sorted(path.name for path in storage.runs_dir(paper_id).iterdir())

    import transit_scholar.layer2.schema_extraction.api as schema_api

    def fail_provider(*_args, **_kwargs):
        raise provider_error

    monkeypatch.setattr(schema_api, "resolve_runtime_llm_client", fail_provider)
    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/workspaces/{workspace_id}/papers/{paper_id}/schema/materialize"
            )
            assert response.status_code == 503, response.text
            assert response.json() == {
                "error": {
                    "code": "PROVIDER_UNAVAILABLE",
                    "message": "Provider is temporarily unavailable",
                    "details": {},
                }
            }
            assert "private" not in response.text
            reservation = app.state.execution_manager.reserve()
            reservation.release()

        assert storage.current_path(paper_id).read_bytes() == pointer_before
        assert storage.read_current(paper_id).run_id == stable_run_id
        assert sorted(path.name for path in storage.runs_dir(paper_id).iterdir()) == runs_before
    finally:
        context.session_factory.kw["bind"].dispose()


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("provider_boundary", ["llm", "embedding"])
def test_wiki_provider_unavailable_is_503_and_preserves_current(
    freeze_root, monkeypatch, provider_boundary
):
    context = context_for(freeze_root)
    paper_id = f"wiki-{provider_boundary}"
    workspace_id, _ = _seed_bound_schema(context, paper_id=paper_id)

    from test_l3s1_wiki_workspace import _offline_composition

    product = context.create_product()
    wiki = product._workspace_wiki()
    wiki._composition_factory = _offline_composition
    stable = wiki.build(workspace_id)
    assert stable.result.manifest.build_status == "complete"
    product.close()

    wiki_dir = workspace_layout(workspace_id, data_root=freeze_root).wiki_dir
    tree_before = _tree_snapshot(wiki_dir)
    assert tree_before

    import transit_scholar.layer2.wiki.providers as wiki_providers

    if provider_boundary == "llm":
        def fail_llm(*_args, **_kwargs):
            raise LLMUnavailableError("private wiki provider URL")

        monkeypatch.setattr(wiki_providers, "resolve_runtime_llm_client", fail_llm)
    else:
        monkeypatch.setattr(
            wiki_providers,
            "resolve_runtime_llm_client",
            lambda *_args, **_kwargs: FakeLLMProvider(),
        )

        def fail_embedding(*_args, **_kwargs):
            raise UnavailableError("private embedding timeout")

        monkeypatch.setattr(
            wiki_providers,
            "resolve_wiki_embedding_provider",
            fail_embedding,
        )

    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/v1/workspaces/{workspace_id}/wiki/build")
            assert response.status_code == 503, response.text
            assert response.json() == {
                "error": {
                    "code": "PROVIDER_UNAVAILABLE",
                    "message": "Provider is temporarily unavailable",
                    "details": {},
                }
            }
            assert "private" not in response.text
            reservation = app.state.execution_manager.reserve()
            reservation.release()
            assert client.get(f"/api/v1/workspaces/{workspace_id}/wiki/status").json()[
                "status"
            ] == "ready"

        assert _tree_snapshot(wiki_dir) == tree_before
    finally:
        context.session_factory.kw["bind"].dispose()
