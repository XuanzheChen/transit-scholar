"""Freeze gate: real HTTP controls over the official runtime composition."""
from pathlib import Path
from uuid import uuid4
import shutil
import pytest
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from transit_scholar.api import create_app
from transit_scholar.api.runtime_context import ApiRuntimeContext
from transit_scholar.config import Settings
from transit_scholar.db.base import Base
from transit_scholar.db.models import AgentRun, ResearchSession, ConversationTurn, Paper
from transit_scholar.layer3.evidence import ResearchEvidence, EvidenceLocator, QueryProvenance
from transit_scholar.layer2.schema_catalog import SchemaCatalog
from transit_scholar.layer3.agent import RoleId
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer3.tools import RetrievalResultEnvelope
from transit_scholar.product.runtime import RuntimeFactory, FileRunResearchStateStore


@pytest.fixture
def freeze_root():
    # Keep real UUID artifact paths below the Windows path-length limit.
    root = (Path('temp') / f'freeze-{uuid4().hex[:8]}').resolve()
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class Lifecycle:
    episodic_store = object()

    def configure_authoritative_readers(self, **kwargs):
        pass

    def maintain_before_session(self, *args, **kwargs):
        pass

    def complete_agent_run(self, **kwargs):
        pass


def context_for(root, **runtime_options):
    settings = Settings(data_root=root)
    settings.init_directories()
    engine = create_engine(f"sqlite:///{root / 'freeze.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    context = ApiRuntimeContext(settings)
    context.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    context.schema_catalog = SchemaCatalog(root)
    context.runtime_factory = RuntimeFactory(
        session_factory=context.session_factory, data_root=root,
        l3s7_lifecycle=Lifecycle(), episodic_memory=SimpleNamespace(retrieve=lambda **kwargs: ()),
        run_config=RunRuntimeConfig(max_episodic_memory_candidates=0),
        **runtime_options,
    )
    return context


def test_real_http_pause_resume_continues_committed_action_once(freeze_root):
    entered, release, released = Event(), Event(), Event()
    calls = []
    outcomes = []
    progress = set()

    class Knowledge:
        def retrieve_knowledge(self, query):
            calls.append(query.query_id)
            entered.set()
            assert release.wait(20), "test did not release action"
            return RetrievalResultEnvelope(query=query, evidence_results=[ResearchEvidence(
                evidence_id="freeze-evidence", text="Transit intervention reduces delay.", source_kind="paper",
                locator=EvidenceLocator(workspace_id=query.workspace_id, source_kind="paper", paper_id="freeze-paper", pages=[2], parse_run_id="fixture-parse", canonical_source_version="fixture-parse"),
                query_provenance=QueryProvenance(query_id=query.query_id, session_id=query.session_id),
            )])

    class Policy:
        def decide(self, definition, role_input, state, role_context, repair_context=None):
            if definition.role_id == RoleId.QUERY_PLANNING:
                return {"completed": True, "proposed_queries": ["freeze question"]}
            if definition.role_id == RoleId.RESEARCH_COORDINATOR:
                next_role = ("query_planning" if not calls else "evidence_reasoning" if "evidence" not in progress
                             else "claim_reasoning" if "claim" not in progress else "final_synthesis")
                return {"completed": True, "next_role_id": next_role}
            if definition.role_id == RoleId.EVIDENCE_REASONING:
                progress.add("evidence")
                return {"completed": True, "admitted_evidence_ids": ["freeze-evidence"]}
            if definition.role_id == RoleId.CLAIM_REASONING:
                progress.add("claim")
                return {"completed": True, "proposed_claims": [{"statement": "Transit intervention reduces delay.", "evidence_ids": list(role_input.accepted_evidence_ids)}]}
            return {"completed": True, "answer_text": "Freeze answer", "citation_references": [item.evidence_id for item in role_input.accepted_evidence]}

    def coordinate(snapshot):
        return RunDecision(mode="complete", completion_reason="done") if snapshot.session_outcomes else RunDecision(mode="direct_session", proposed_questions=["freeze question"])

    context = context_for(freeze_root, policies={role: Policy() for role in RoleId},
                          knowledge_service=Knowledge(), coordinator=coordinate)
    app = create_app(runtime_context=context)
    manager = app.state.execution_manager
    original_release = manager._release

    def on_release(*args):
        try:
            outcomes.append(args[1].result())
        except Exception as exc:
            outcomes.append(repr(exc))
        original_release(*args)
        released.set()

    manager._release = on_release
    try:
        with TestClient(app) as client:
            assert client.get('/api/v1/health').status_code == 200
            assert client.get('/api/v1/capabilities').json()['pause_resume']
            workspace = client.post('/api/v1/workspaces', json={'name': 'Freeze'}).json()['workspace_id']
            with context.session_factory() as fresh:
                fresh.add(Paper(id="freeze-paper", title="Transit study", status="active"))
                fresh.commit()
            assert client.post(f'/api/v1/workspaces/{workspace}/papers', json={"paper_id": "freeze-paper"}).status_code in (200, 201)
            conversation = client.post(f'/api/v1/workspaces/{workspace}/conversations', json={'title': 'Freeze'}).json()['conversation_id']
            response = client.post(f'/api/v1/conversations/{conversation}/turns', json={'message': 'Freeze question'})
            assert response.status_code == 202, response.text
            run_id = response.json()['agent_run_id']
            assert entered.wait(20), str(outcomes)
            future = manager.future(run_id)
            for endpoint in (f'/api/v1/workspaces/{workspace}/papers/freeze-paper/schema/materialize', f'/api/v1/workspaces/{workspace}/wiki/build'):
                blocked = client.post(endpoint)
                assert blocked.status_code == 409
                assert blocked.json()['error']['code'] == 'WORKSPACE_BUSY'
            assert client.post(f'/api/v1/runs/{run_id}/pause').status_code == 200
            release.set()
            assert future.result(timeout=20)['status'] == 'paused'
            assert released.wait(20)
            checkpoint = FileRunResearchStateStore(context.runtime_factory.runtime_root).load(run_id)
            session_id = checkpoint['orchestration_state']['current_research_session_id']
            with context.session_factory() as fresh:
                assert fresh.get(AgentRun, run_id).status == 'paused'
                assert fresh.get(ResearchSession, session_id).status == 'paused'
                main = ResearchStateService(fresh).load_research_state(agent_run_id=run_id, research_session_id=session_id).payload['l3s5']
                role_id = main['current_role_execution_id']
                assert main['status'] == 'paused'
                assert 'run.paused' in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run_id)]
            from transit_scholar.layer3.runtime import FileRoleExecutionStore
            roles = FileRoleExecutionStore(context.runtime_factory.runtime_root / run_id / 'roles')
            role = roles.load(role_id)
            assert role.status == 'paused'
            assert role.working_state.next_action_index == 2
            assert len(role.working_state.intermediate_artifacts) == 2
            assert len(calls) == 1
            assert client.get(f'/api/v1/runs/{run_id}').json()['status'] == 'paused'
            released.clear()
            response = client.post(f'/api/v1/runs/{run_id}/resume')
            assert response.status_code == 202, response.text
            assert released.wait(20)
            with context.session_factory() as fresh:
                assert fresh.get(AgentRun, run_id).status == 'completed'
                assert [s.id for s in fresh.scalars(select(ResearchSession).where(ResearchSession.agent_run_id == run_id))] == [session_id]
                turn = fresh.scalar(select(ConversationTurn).where(ConversationTurn.agent_run_id == run_id))
                assert turn.status == 'completed'
                assert turn.final_assistant_response['answer_text']
                admitted_ids = turn.final_assistant_response['citation_refs']
                assert len(admitted_ids) == 1, str(outcomes)
                assert admitted_ids != ['freeze-evidence']
                assert 'run.completed' in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run_id)]
            assert roles.load(role_id).status == 'completed'
            assert len(roles.load(role_id).working_state.intermediate_artifacts) == 2
            assert len(calls) == 1
            timeline = client.get(f'/api/v1/runs/{run_id}/timeline').json()
            with context.session_factory() as fresh:
                completed = [e for e in AgentTraceService(fresh).read_trace(agent_run_id=run_id) if e.event_type == 'run.completed'][0]
            assert completed.sequence in [event['sequence'] for event in timeline['events']]
            turn_view = client.get(f'/api/v1/conversations/{conversation}').json()['turns'][0]
            assert turn_view['final_answer']
            assert turn_view['assistant_response']['citation_references'] == admitted_ids
            assert 'citation_refs' not in turn_view['assistant_response']
            assert [item['evidence_id'] for item in turn_view['answer_citations']] == admitted_ids
            public_data = [event['data'] for event in timeline['events']]
            assert any(data.get('research_question') == 'freeze question' for data in public_data)
            assert any(data.get('query_text') == 'freeze question' for data in public_data)
            assert any(data.get('preview') == 'Transit intervention reduces delay.' and data.get('paper_id') == 'freeze-paper' for data in public_data)
            assert any(data.get('statement') == 'Transit intervention reduces delay.' for data in public_data)
    finally:
        release.set()
        manager.shutdown()
        context.session_factory.kw['bind'].dispose()
