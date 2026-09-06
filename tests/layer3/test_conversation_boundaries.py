from transit_scholar.db.models import AgentRun, ResearchSession


def test_agent_core_models_do_not_own_conversation_state():
    assert "conversation_id" not in AgentRun.__table__.columns
    assert "turn_id" not in AgentRun.__table__.columns
    assert "conversation_id" not in ResearchSession.__table__.columns
    assert "turn_id" not in ResearchSession.__table__.columns
