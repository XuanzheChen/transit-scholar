"""Regression gate for the default product composition, without a research run."""

from pathlib import Path
from unittest.mock import Mock

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from transit_scholar.config import Settings, settings as global_settings
from transit_scholar.db.base import Base
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product import bootstrap
from transit_scholar.product.conversation import ConversationGoalOutput


class FakeStructuredLLM:
    def __init__(self):
        self.calls = []

    def generate_structured(self, messages, output_schema, metadata=None):
        self.calls.append((messages, output_schema, metadata))
        assert output_schema is ConversationGoalOutput
        return ConversationGoalOutput(resolved_user_goal="Compare transit studies")


def test_default_bootstrap_migrates_product_target_and_shares_semantic_client(
    tmp_path, monkeypatch,
):
    settings = Settings(data_root=tmp_path / "product")
    # Keep both targets isolated and distinct; restore ambient settings at teardown.
    ambient_root = tmp_path / "ambient"
    monkeypatch.setattr(global_settings, "data_root", ambient_root)
    fake_client = FakeStructuredLLM()
    resolver = Mock(return_value=fake_client)
    monkeypatch.setattr(bootstrap, "resolve_runtime_llm_client", resolver)
    create_all = Mock(side_effect=AssertionError("formal bootstrap must use Alembic"))
    monkeypatch.setattr(Base.metadata, "create_all", create_all)

    product = bootstrap.build_local_product(settings)
    engine = product.session.get_bind()
    try:
        factory = product.research.runtime_factory
        assert factory.settings is settings
        assert factory.data_root == settings.data_root
        assert factory.llm_client is fake_client
        assert Path(engine.url.database).resolve() == settings.database_path.resolve()
        actual_path = product.session.execute(text("PRAGMA database_list")).one()[2]
        assert Path(actual_path).resolve() == settings.database_path.resolve()
        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        assert product.session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ScriptDirectory.from_config(cfg).get_current_head()
        assert not ambient_root.exists()
        create_all.assert_not_called()

        workspace = WorkspaceService(product.session).create(name="bootstrap").workspace
        conversation = product.create_conversation(workspace.workspace_id)
        prior = product.conversations.create_turn(
            conversation.id, "Find transit studies", status="completed",
            resolved_user_goal="Find transit studies",
            final_assistant_response={"answer": "Found studies"},
        )
        assert product.research.goal_resolver.resolve("Compare them", [prior]) == "Compare transit studies"
        assert len(fake_client.calls) == 1
        assert fake_client.calls[0][2]["prompt_key"] == "conversation_goal_resolver"

        run = product.research.execution.create_agent_run(
            workspace_id=workspace.workspace_id, user_goal="Compare transit studies",
        )
        product.session.commit()
        scope = factory.build_run_scope(run.agent_run_id)
        try:
            assert scope.l3s7_lifecycle.distiller.client is fake_client
            assert scope.l3s7_lifecycle.promotion_service.role.client is fake_client
            assert scope.session.get_bind().url == engine.url
        finally:
            scope.close()
        resolver.assert_called_once_with()
    finally:
        product.close()
        engine.dispose()
