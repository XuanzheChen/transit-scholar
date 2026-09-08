"""Application-scope composition for the formal API."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from transit_scholar.config import Settings
from transit_scholar.db.engine import engine_for
from transit_scholar.db.lifecycle import alembic_upgrade_head
from transit_scholar.layer2.schema_catalog import SchemaCatalog
from transit_scholar.layer2.schema_extraction.errors import LLMUnavailableError
from transit_scholar.product.conversation import ConversationGoalOutput, ConversationGoalResolver
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.runtime import RuntimeFactory


class ApiRuntimeContext:
    """Shared, process-safe dependencies with fresh facade/session scopes."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.session_factory = None
        self.runtime_factory = None
        self.schema_catalog = None
        self._llm_client = None

    def initialize(self) -> "ApiRuntimeContext":
        if self.session_factory is not None:
            return self
        try:
            from transit_scholar.product.bootstrap import build_local_product
            bootstrapped = build_local_product(self.settings)
            bind = bootstrapped.session.get_bind()
            self.session_factory = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True)
            self._llm_client = getattr(bootstrapped.research.runtime_factory, "llm_client", None)
            self.runtime_factory = bootstrapped.research.runtime_factory
            bootstrapped.close()
        except LLMUnavailableError:
            self.settings.init_directories()
            from transit_scholar.config import settings as global_settings
            global_settings.data_root = self.settings.data_root
            global_settings.init_directories()
            alembic_upgrade_head()
            self.session_factory = sessionmaker(bind=engine_for(self.settings.database_url), autoflush=False, autocommit=False, future=True)
            self._llm_client = None
        if self._llm_client is None:
            self.runtime_factory = None
        self.schema_catalog = SchemaCatalog(self.settings.data_root)
        return self

    @property
    def agent_runtime_available(self) -> bool:
        return self.runtime_factory is not None

    def _goal_resolver(self):
        if self._llm_client is None:
            return None
        client = self._llm_client
        def generate_goal(message, prior_turns):
            context = [{"user_message": t.user_message, "resolved_user_goal": t.resolved_user_goal,
                        "assistant_response": t.final_assistant_response} for t in prior_turns]
            return client.generate_structured([
                {"role": "system", "content": "Resolve the current message into one standalone research goal using prior conversation context. Return only schema-valid JSON."},
                {"role": "user", "content": json.dumps({"current_message": message, "prior_turns": context}, ensure_ascii=False)},
            ], ConversationGoalOutput, metadata={"prompt_key": "conversation_goal_resolver"})
        return ConversationGoalResolver(generate_goal)

    def create_product(self) -> TransitScholarProduct:
        self.initialize()
        return TransitScholarProduct(
            self.session_factory(), self.runtime_factory,
            goal_resolver=self._goal_resolver(), data_root=self.settings.data_root,
            schema_catalog=self.schema_catalog,
        )
