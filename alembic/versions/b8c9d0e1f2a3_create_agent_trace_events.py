"""create_agent_trace_events

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-30 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("trace_sequence", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "agent_trace_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("agent_run_id", sa.String(length=32), nullable=False),
        sa.Column("research_session_id", sa.String(length=32), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_trace_events_sequence_positive"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["research_session_id"], ["research_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_agent_trace_events_run_sequence"),
    )
    op.create_index("ix_agent_trace_events_agent_run_sequence", "agent_trace_events", ["agent_run_id", "sequence"], unique=False)
    op.create_index("ix_agent_trace_events_research_session_id", "agent_trace_events", ["research_session_id"], unique=False)
    op.create_index("ix_agent_trace_events_event_type", "agent_trace_events", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agent_trace_events_event_type", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_research_session_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_agent_run_sequence", table_name="agent_trace_events")
    op.drop_table("agent_trace_events")
    op.drop_column("agent_runs", "trace_sequence")
