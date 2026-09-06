"""create product conversation tables"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("conversation_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("title", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_conversation_sessions_workspace_id", "conversation_sessions", ["workspace_id"])
    op.create_table("conversation_turns",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("conversation_id", sa.String(32), sa.ForeignKey("conversation_sessions.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False), sa.Column("user_message", sa.Text, nullable=False),
        sa.Column("resolved_user_goal", sa.Text), sa.Column("agent_run_id", sa.String(32)),
        sa.Column("final_assistant_response", sa.JSON), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.String(2048)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_turns_sequence"),
        sa.CheckConstraint("status IN ('preparing', 'running', 'completed', 'failed')", name="ck_conversation_turns_status"),
    )
    op.create_index("ix_conversation_turns_conversation_sequence", "conversation_turns", ["conversation_id", "sequence"])

def downgrade():
    op.drop_table("conversation_turns")
    op.drop_index("ix_conversation_sessions_workspace_id", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
