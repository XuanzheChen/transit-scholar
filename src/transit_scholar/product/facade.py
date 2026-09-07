from .conversation import ConversationService
from .projection import ProductStateProjector
from .research import ResearchService


class TransitScholarProduct:
    def __init__(self, session, runtime_factory, *, goal_resolver=None):
        self.session = session
        self.conversations = ConversationService(session)
        self.research = ResearchService(session, runtime_factory, conversations=self.conversations, goal_resolver=goal_resolver)
        self.projector = ProductStateProjector(session, runtime_factory)

    def create_conversation(self, workspace_id, title=None):
        conversation = self.conversations.create_session(workspace_id, title)
        self.session.commit()
        return conversation
    create_session = create_conversation
    def list_conversations(self, workspace_id): return self.conversations.list_sessions(workspace_id)
    def get_conversation(self, conversation_id):
        return self.conversations.get_session(conversation_id)

    def read_turn(self, turn_id):
        return self.conversations.get_turn(turn_id)

    def list_turns(self, conversation_id):
        return self.conversations.list_turns(conversation_id)
    def read_conversation(self, conversation_id): return self.projector.conversation(conversation_id)
    read_conversation_state = read_conversation
    conversation_state = read_conversation
    def submit_message(self, conversation_id, message): return self.research.submit_message(conversation_id, message)
    def execute_run(self, agent_run_id, **kwargs): return self.research.execute_run(agent_run_id, **kwargs)
    def resume_run(self, agent_run_id): return self.research.resume_run(agent_run_id)
    def read_run_state(self, agent_run_id): return self.projector.run_state(agent_run_id)

    def close(self):
        """Release the facade's long-lived SQLAlchemy session."""
        self.session.close()
