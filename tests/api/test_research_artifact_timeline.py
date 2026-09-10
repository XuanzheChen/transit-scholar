"""HTTP projections resolve owned research objects, never trace diagnostics."""
import json
from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.db.models import AgentRun, ResearchSession, ResearchQueryRecord, EvidenceRecord, ClaimRecord, Paper
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer3.run_context import RunFinalResponseArtifact
from test_freeze_runtime_integration import context_for, freeze_root


def test_timeline_and_citations_filter_foreign_missing_and_raw_payloads(freeze_root):
    context = context_for(freeze_root)
    product = context.create_product()
    workspace = product.create_workspace('Artifact ownership')
    conversation = product.create_conversation(workspace.workspace_id, 'Artifacts')
    session = product.session
    session.add(Paper(id='paper', title='A transit study', status='active'))
    for prefix in ('own', 'foreign'):
        session.add(AgentRun(id=f'{prefix}-run', workspace_id=workspace.workspace_id, workspace_revision=workspace.revision, user_goal='goal', status='completed'))
        session.add(ResearchSession(id=f'{prefix}-session', agent_run_id=f'{prefix}-run', research_question=f'{prefix} research question', status='completed'))
        session.add(ResearchQueryRecord(id=f'{prefix}-query', research_session_id=f'{prefix}-session', query_text=f'{prefix} query text', status='completed'))
        session.add(EvidenceRecord(id=f'{prefix}-evidence', research_session_id=f'{prefix}-session', source_query_id=f'{prefix}-query',
                                  locator_json=json.dumps({'paper_id': 'paper', 'pages': [2], 'path': '/SECRET/storage/path'}),
                                  text_snapshot=f'{prefix} evidence ' + 'x' * 600,
                                  source_metadata_json=json.dumps({'provider_reasoning': 'SECRET'}), retrieval_provenance_json='{}'))
        session.add(ClaimRecord(id=f'{prefix}-claim', research_session_id=f'{prefix}-session', statement=f'{prefix} claim statement', status='supported', rationale='SECRET rationale'))
    session.flush()
    trace = AgentTraceService(session)
    for event, identities in (
        ('run.session.created', {}),
        ('query.created', {'query_id': 'own-query'}),
        ('evidence.admitted', {'evidence_id': 'own-evidence'}),
        ('claim.created', {'claim_id': 'own-claim'}),
        ('claim.created', {'claim_id': 'foreign-claim', 'query_id': 'foreign-query', 'evidence_id': 'foreign-evidence'}),
        ('claim.created', {'claim_id': 'missing', 'query_id': 'missing', 'evidence_id': 'missing'}),
        ('runtime.action', {'action_type': 'CREATE_QUERY', 'action_result': {'value': {'query_id': 'own-query', 'query_text': 'SECRET forged query'}}}),
    ):
        trace.append_event(agent_run_id='own-run', research_session_id='own-session', event_type=event,
                           payload={**identities, 'research_question': 'SECRET prompt', 'query_text': 'SECRET query',
                                    'statement': 'SECRET statement', 'provider_reasoning': 'SECRET reasoning', 'scratchpad': 'SECRET scratchpad'})
    artifact = RunFinalResponseArtifact(answer_text='Answer', citation_refs=['foreign-evidence', 'missing', 'own-evidence', 'own-evidence'], source_refs=['foreign-evidence'])
    product.conversations.create_turn(conversation.id, 'Question', agent_run_id='own-run', status='completed', final_assistant_response=artifact.model_dump(mode='json'))
    session.commit()
    product.close()
    try:
        with TestClient(create_app(runtime_context=context)) as client:
            response = client.get('/api/v1/runs/own-run/timeline')
            assert response.status_code == 200
            assert 'SECRET' not in response.text
            data = [event['data'] for event in response.json()['events']]
            assert data[0]['research_question'] == 'own research question'
            assert data[1]['query_text'] == 'own query text'
            assert data[2]['paper_id'] == 'paper'
            assert data[2]['pages'] == [2]
            assert len(data[2]['preview']) == 500
            assert data[3]['statement'] == 'own claim statement'
            assert data[3]['status'] == 'supported'
            for omitted in data[4:6]:
                assert not {'statement', 'query_text', 'preview', 'pages', 'paper_id'} & omitted.keys()
            assert data[6]['query_text'] == 'own query text'
            assert 'foreign claim statement' not in response.text
            assert 'foreign evidence' not in response.text
            incremental = client.get('/api/v1/runs/own-run/timeline?after_sequence=4').json()
            assert [event['sequence'] for event in incremental['events']] == [5, 6, 7]
            assert incremental['next_sequence'] == 7
            view = client.get(f'/api/v1/conversations/{conversation.id}').json()['turns'][0]
            assert [item['evidence_id'] for item in view['answer_citations']] == ['own-evidence']
            assert 'citation_refs' not in view['assistant_response']
    finally:
        context.session_factory.kw['bind'].dispose()
