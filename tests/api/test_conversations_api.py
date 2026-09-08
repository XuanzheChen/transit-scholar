from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from sqlalchemy import select

from transit_scholar.api import create_app
from transit_scholar.api.dependencies import get_product
from transit_scholar.db.models import (
    AgentRun, ConversationSession, ConversationTurn, EvidenceRecord, Paper,
    ResearchQueryRecord, ResearchSession,
)
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.errors import ProductValidationError
import json


class RecordingExecutionManager:
    def __init__(self):
        self.submitted_run_ids = []
        self._busy = False

    def reserve(self):
        if self._busy:
            from transit_scholar.api.runtime import RunnerBusyError
            raise RunnerBusyError()
        self._busy = True
        return self

    def release(self):
        self._busy = False

    def submit_reserved(self, _reservation, agent_run_id: str):
        self.submitted_run_ids.append(agent_run_id)

    def shutdown(self, wait: bool = True):
        pass


class HoldingExecutionManager:
    def __init__(self):
        self._lock = Lock()
        self._busy = False
        self.submitted_run_ids = []

    def reserve(self):
        with self._lock:
            if self._busy:
                from transit_scholar.api.runtime import RunnerBusyError
                raise RunnerBusyError()
            self._busy = True
        return self

    def release(self):
        with self._lock:
            self._busy = False

    def submit_reserved(self, _reservation, agent_run_id: str):
        self.submitted_run_ids.append(agent_run_id)

    def shutdown(self, wait: bool = True):
        pass


class FailingExecutionManager(RecordingExecutionManager):
    def submit_reserved(self, _reservation, agent_run_id: str):
        raise RuntimeError("executor unavailable")


def test_conversation_creation_and_prompt_submission_are_non_blocking(session, project_tmp_path):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    workspace = product.create_workspace("Conversation API")
    app = create_app(data_root=project_tmp_path)
    manager = RecordingExecutionManager()
    app.state.execution_manager = manager
    app.dependency_overrides[get_product] = lambda: product

    with TestClient(app) as client:
        created = client.post(
            f"/api/v1/workspaces/{workspace.workspace_id}/conversations",
            json={"title": "Transit research"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation_id"]

        submitted = client.post(
            f"/api/v1/conversations/{conversation_id}/turns",
            json={"message": "Research transit demand"},
        )
        assert submitted.status_code == 202
        ids = submitted.json()
        assert manager.submitted_run_ids == [ids["agent_run_id"]]

        conversation = client.get(f"/api/v1/conversations/{conversation_id}")
        assert conversation.status_code == 200
        turn = conversation.json()["turns"][0]
        assert turn["turn_id"] == ids["turn_id"]
        assert turn["agent_run_id"] == ids["agent_run_id"]
        assert turn["user_message"] == "Research transit demand"
        assert turn["status"] == "preparing"

        read_turn = client.get(f"/api/v1/turns/{ids['turn_id']}")
        assert read_turn.status_code == 200
        assert read_turn.json()["resolved_user_goal"] == "Research transit demand"


def test_completed_turn_exposes_admitted_evidence_citations(session, project_tmp_path):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    workspace = product.create_workspace("Citation API")
    paper = Paper(id="paper-api-cite", title="Persisted Paper", status="active")
    conversation = ConversationSession(id="conversation-api-cite", workspace_id=workspace.workspace_id)
    run = AgentRun(id="run-api-cite", workspace_id=workspace.workspace_id, user_goal="Answer", status="completed", workspace_revision=1)
    research_session = ResearchSession(id="session-api-cite", agent_run_id=run.id, research_question="Question", status="completed")
    query = ResearchQueryRecord(id="query-api-cite", research_session_id=research_session.id, query_text="Query", status="completed")
    evidence = EvidenceRecord(
        id="evidence-api-cite", research_session_id=research_session.id, source_query_id=query.id,
        locator_json=json.dumps({"workspace_id": workspace.workspace_id, "source_kind": "paper", "paper_id": paper.id, "pages": [3], "block_id": "block-3"}),
        text_snapshot="Persisted admitted quote.",
        source_metadata_json=json.dumps({"source_kind": "paper", "paper_provenance": {"title": paper.title}}),
        retrieval_provenance_json="{}",
    )
    turn = ConversationTurn(
        id="turn-api-cite", conversation_id=conversation.id, sequence=1, user_message="Question",
        agent_run_id=run.id, status="completed",
        final_assistant_response={"answer_text": "Completed answer", "citation_references": [evidence.id]},
    )
    session.add_all([paper, conversation, run, research_session, query, evidence, turn])
    session.flush()
    app = create_app(data_root=project_tmp_path)
    app.dependency_overrides[get_product] = lambda: product

    with TestClient(app) as client:
        response = client.get(f"/api/v1/turns/{turn.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["final_answer"] == "Completed answer"
    assert body["answer_citations"] == [{
        "evidence_id": evidence.id, "research_session_id": research_session.id,
        "paper_id": paper.id, "paper_title": paper.title, "source_kind": "paper",
        "pages": [3], "block_id": "block-3", "character_start": None,
        "character_end": None, "parse_run_id": None,
        "canonical_source_version": None, "evidence_quote": evidence.text_snapshot,
    }]


def test_racing_prompt_submissions_admit_only_one_before_product_mutation(project_tmp_path):
    app = create_app(data_root=project_tmp_path)
    manager = HoldingExecutionManager()
    app.state.execution_manager = manager

    with TestClient(app) as client:
        workspace = client.post("/api/v1/workspaces", json={"name": "Race workspace"})
        assert workspace.status_code == 201
        conversation = client.post(
            f"/api/v1/workspaces/{workspace.json()['workspace_id']}/conversations",
            json={"title": "Race conversation"},
        )
        assert conversation.status_code == 201
        conversation_id = conversation.json()["conversation_id"]
        barrier = Barrier(2)

        def submit(message: str):
            barrier.wait(timeout=2)
            return client.post(
                f"/api/v1/conversations/{conversation_id}/turns",
                json={"message": message},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, ("First prompt", "Second prompt")))

        accepted = [response for response in responses if response.status_code == 202]
        rejected = [response for response in responses if response.status_code == 409]
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert rejected[0].json()["error"]["code"] == "RUNNER_BUSY"

        product = app.state.runtime_context.create_product()
        try:
            turns = list(product.session.scalars(select(ConversationTurn)).all())
            runs = list(product.session.scalars(select(AgentRun)).all())
        finally:
            product.close()

    assert len(turns) == 1
    assert len(runs) == 1
    assert runs[0].status == "created"
    assert manager.submitted_run_ids == [accepted[0].json()["agent_run_id"]]


def test_scheduling_failure_discards_prepared_turn_and_run(project_tmp_path):
    app = create_app(data_root=project_tmp_path)
    app.state.execution_manager = FailingExecutionManager()

    with TestClient(app) as client:
        workspace = client.post("/api/v1/workspaces", json={"name": "Failure workspace"})
        conversation = client.post(
            f"/api/v1/workspaces/{workspace.json()['workspace_id']}/conversations",
            json={"title": "Failure conversation"},
        )
        response = client.post(
            f"/api/v1/conversations/{conversation.json()['conversation_id']}/turns",
            json={"message": "Cannot schedule"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"

        product = app.state.runtime_context.create_product()
        try:
            assert list(product.session.scalars(select(ConversationTurn)).all()) == []
            assert list(product.session.scalars(select(AgentRun)).all()) == []
        finally:
            product.close()


def test_prompt_preparation_validation_uses_stable_error_envelope(session, project_tmp_path, monkeypatch):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    workspace = product.create_workspace("Validation workspace")
    conversation = product.create_conversation(workspace.workspace_id)
    app = create_app(data_root=project_tmp_path)
    app.dependency_overrides[get_product] = lambda: product

    def reject(_conversation_id, _message):
        raise ProductValidationError("goal generator is required")

    monkeypatch.setattr(product, "prepare_message", reject)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/conversations/{conversation.id}/turns",
            json={"message": "Follow up"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "goal generator is required",
        "details": {"conversation_id": conversation.id},
    }
