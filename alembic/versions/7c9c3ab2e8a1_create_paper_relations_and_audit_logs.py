"""create_paper_relations_and_audit_logs

Revision ID: 7c9c3ab2e8a1
Revises: 5a7bec75e342
Create Date: 2026-07-25 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c9c3ab2e8a1'
down_revision: Union[str, Sequence[str], None] = '5a7bec75e342'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('paper_relations',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('source_paper_id', sa.String(length=32), nullable=False),
    sa.Column('target_paper_id', sa.String(length=32), nullable=False),
    sa.Column('relation_type', sa.String(length=64), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('reasons_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.String(length=128), nullable=True),
    sa.ForeignKeyConstraint(['source_paper_id'], ['papers.id'], ),
    sa.ForeignKeyConstraint(['target_paper_id'], ['papers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.CheckConstraint('source_paper_id != target_paper_id', name='ck_paper_relations_no_self'),
    sa.UniqueConstraint('source_paper_id', 'target_paper_id', 'relation_type', name='uq_paper_relations_triple')
    )
    op.create_index('ix_paper_relations_confidence', 'paper_relations', ['confidence'], unique=False)
    op.create_index('ix_paper_relations_relation_type', 'paper_relations', ['relation_type'], unique=False)
    op.create_index('ix_paper_relations_source_paper_id', 'paper_relations', ['source_paper_id'], unique=False)
    op.create_index('ix_paper_relations_status', 'paper_relations', ['status'], unique=False)
    op.create_index('ix_paper_relations_target_paper_id', 'paper_relations', ['target_paper_id'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=32), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('actor_type', sa.String(length=64), nullable=False),
    sa.Column('old_value_json', sa.Text(), nullable=True),
    sa.Column('new_value_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_logs_actor_type', 'audit_logs', ['actor_type'], unique=False)
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['entity_type', 'entity_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_audit_logs_entity', table_name='audit_logs')
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_type', table_name='audit_logs')
    op.drop_index('ix_audit_logs_action', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_paper_relations_target_paper_id', table_name='paper_relations')
    op.drop_index('ix_paper_relations_status', table_name='paper_relations')
    op.drop_index('ix_paper_relations_source_paper_id', table_name='paper_relations')
    op.drop_index('ix_paper_relations_relation_type', table_name='paper_relations')
    op.drop_index('ix_paper_relations_confidence', table_name='paper_relations')
    op.drop_table('paper_relations')
