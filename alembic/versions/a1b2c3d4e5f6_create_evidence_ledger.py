"""create_evidence_ledger

Revision ID: a1b2c3d4e5f6
Revises: d3e4f5a6b7c8
Create Date: 2026-08-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("research_session_id", sa.String(length=32), nullable=False),
        sa.Column("source_query_id", sa.String(length=32), nullable=False),
        sa.Column("locator_json", sa.Text(), nullable=False),
        sa.Column("text_snapshot", sa.Text(), nullable=False),
        sa.Column("source_metadata_json", sa.Text(), nullable=False),
        sa.Column("retrieval_provenance_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["research_session_id"], ["research_sessions.id"]),
        sa.ForeignKeyConstraint(["source_query_id"], ["research_query_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_records_research_session_id", "evidence_records", ["research_session_id"], unique=False)
    op.create_index("ix_evidence_records_source_query_id", "evidence_records", ["source_query_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_evidence_records_source_query_id", table_name="evidence_records")
    op.drop_index("ix_evidence_records_research_session_id", table_name="evidence_records")
    op.drop_table("evidence_records")
