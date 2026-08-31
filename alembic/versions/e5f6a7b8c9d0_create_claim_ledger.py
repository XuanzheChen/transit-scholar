"""create_claim_ledger

Revision ID: e5f6a7b8c9d0
Revises: a1b2c3d4e5f6
Create Date: 2026-08-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "claim_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("research_session_id", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("status IN ('proposed', 'supported', 'conflicting', 'rejected')", name="ck_claim_records_status"),
        sa.ForeignKeyConstraint(["research_session_id"], ["research_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claim_records_research_session_id", "claim_records", ["research_session_id"], unique=False)
    op.create_index("ix_claim_records_status", "claim_records", ["status"], unique=False)
    op.create_table(
        "claim_evidence_links",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("claim_id", sa.String(length=32), nullable=False),
        sa.Column("evidence_id", sa.String(length=32), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("relation IN ('supports', 'contradicts')", name="ck_claim_evidence_links_relation"),
        sa.ForeignKeyConstraint(["claim_id"], ["claim_records.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence_links_pair"),
    )
    op.create_index("ix_claim_evidence_links_claim_id", "claim_evidence_links", ["claim_id"], unique=False)
    op.create_index("ix_claim_evidence_links_evidence_id", "claim_evidence_links", ["evidence_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_claim_evidence_links_evidence_id", table_name="claim_evidence_links")
    op.drop_index("ix_claim_evidence_links_claim_id", table_name="claim_evidence_links")
    op.drop_table("claim_evidence_links")
    op.drop_index("ix_claim_records_status", table_name="claim_records")
    op.drop_index("ix_claim_records_research_session_id", table_name="claim_records")
    op.drop_table("claim_records")
