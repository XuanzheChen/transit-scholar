"""create_research_query_ledger

Revision ID: d3e4f5a6b7c8
Revises: b8c9d0e1f2a3
Create Date: 2026-08-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_query_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("research_session_id", sa.String(length=32), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parent_query_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'completed', 'abandoned')", name="ck_research_query_records_status"),
        sa.ForeignKeyConstraint(["parent_query_id"], ["research_query_records.id"]),
        sa.ForeignKeyConstraint(["research_session_id"], ["research_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_query_records_parent_query_id", "research_query_records", ["parent_query_id"], unique=False)
    op.create_index("ix_research_query_records_research_session_id", "research_query_records", ["research_session_id"], unique=False)
    op.create_index("ix_research_query_records_status", "research_query_records", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_research_query_records_status", table_name="research_query_records")
    op.drop_index("ix_research_query_records_research_session_id", table_name="research_query_records")
    op.drop_index("ix_research_query_records_parent_query_id", table_name="research_query_records")
    op.drop_table("research_query_records")
