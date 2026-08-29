"""create_workspace_tables

Layer3 Stage1 Workspace control plane (REQ-001/REQ-002/REQ-003/REQ-006):

- ``workspaces`` — persistent Workspace domain object with lifecycle status,
  schema mode, optional bound Schema identity triple (schema_id /
  schema_version / schema_hash) and a monotonic revision marker;
- ``workspace_paper_memberships`` — unique Workspace-to-Paper membership rows;
  paper inclusion is never a column on the global ``papers`` table.

Revision ID: e4f5a6b7c8d9
Revises: c2f02a8e1b39
Create Date: 2026-08-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "c2f02a8e1b39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_mode", sa.String(length=16), nullable=False),
        sa.Column("schema_id", sa.String(length=256), nullable=True),
        sa.Column("schema_version", sa.String(length=64), nullable=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleting', 'deleted')",
            name="ck_workspaces_status",
        ),
        sa.CheckConstraint(
            "schema_mode IN ('bound', 'none')", name="ck_workspaces_schema_mode"
        ),
        sa.CheckConstraint(
            "(schema_mode = 'bound' AND schema_id IS NOT NULL "
            "AND schema_version IS NOT NULL AND schema_hash IS NOT NULL) "
            "OR (schema_mode = 'none' AND schema_id IS NULL "
            "AND schema_version IS NULL AND schema_hash IS NULL)",
            name="ck_workspaces_schema_mode_consistency",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_workspaces_revision_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspaces_status", "workspaces", ["status"], unique=False)
    op.create_index(
        "ix_workspaces_schema_mode", "workspaces", ["schema_mode"], unique=False
    )
    op.create_table(
        "workspace_paper_memberships",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("paper_id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "paper_id", name="uq_workspace_paper_membership_pair"
        ),
    )
    op.create_index(
        "ix_workspace_paper_memberships_workspace_id",
        "workspace_paper_memberships",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_paper_memberships_paper_id",
        "workspace_paper_memberships",
        ["paper_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_workspace_paper_memberships_paper_id",
        table_name="workspace_paper_memberships",
    )
    op.drop_index(
        "ix_workspace_paper_memberships_workspace_id",
        table_name="workspace_paper_memberships",
    )
    op.drop_table("workspace_paper_memberships")
    op.drop_index("ix_workspaces_schema_mode", table_name="workspaces")
    op.drop_index("ix_workspaces_status", table_name="workspaces")
    op.drop_table("workspaces")