import json
from .facade import TransitScholarProduct
from .runtime import RuntimeFactory
from sqlalchemy.orm import sessionmaker
from transit_scholar.db.engine import engine_for
from transit_scholar.db.base import Base
import transit_scholar.db.models  # noqa: F401 - register ORM models
from transit_scholar.layer2.schema_extraction.llm import resolve_runtime_llm_client
from .conversation import ConversationGoalOutput, ConversationGoalResolver


def build_local_product(settings=None):
    if settings is None:
        from transit_scholar.config import settings as settings
    settings.init_directories()
    engine = engine_for(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    llm_client = resolve_runtime_llm_client()
    runtime = RuntimeFactory(settings=settings, session_factory=session_factory, llm_client=llm_client)
    session = session_factory()

    def generate_goal(message, prior_turns):
        context = [
            {"user_message": turn.user_message, "resolved_user_goal": turn.resolved_user_goal,
             "assistant_response": turn.final_assistant_response}
            for turn in prior_turns
        ]
        return llm_client.generate_structured(
            [
                {"role": "system", "content": "Resolve the current message into one standalone research goal using prior conversation context. Return only schema-valid JSON."},
                {"role": "user", "content": json.dumps({"current_message": message, "prior_turns": context}, ensure_ascii=False)},
            ],
            ConversationGoalOutput,
            metadata={"prompt_key": "conversation_goal_resolver"},
        )

    return TransitScholarProduct(session, runtime, goal_resolver=ConversationGoalResolver(generate_goal))
