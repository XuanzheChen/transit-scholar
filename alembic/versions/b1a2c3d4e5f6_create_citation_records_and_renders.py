"""create_citation_records_and_renders

Revision ID: b1a2c3d4e5f6
Revises: 7c9c3ab2e8a1
Create Date: 2026-07-25 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1a2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '7c9c3ab2e8a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('citation_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('paper_id', sa.String(length=32), nullable=False),
    sa.Column('source_format', sa.String(length=32), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('structured_json', sa.Text(), nullable=False),
    sa.Column('parse_status', sa.String(length=16), nullable=False),
    sa.Column('parse_warnings_json', sa.Text(), nullable=False),
    sa.Column('is_selected', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_citation_records_paper_id', 'citation_records', ['paper_id'], unique=False)
    op.create_index('ix_citation_records_is_selected', 'citation_records', ['is_selected'], unique=False)
    op.create_index('ix_citation_records_deleted_at', 'citation_records', ['deleted_at'], unique=False)
    op.create_index('ix_citation_records_source_format', 'citation_records', ['source_format'], unique=False)
    op.create_table('citation_renders',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('citation_record_id', sa.String(length=32), nullable=False),
    sa.Column('style', sa.String(length=32), nullable=False),
    sa.Column('locale', sa.String(length=16), nullable=False),
    sa.Column('rendered_text', sa.Text(), nullable=False),
    sa.Column('renderer_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['citation_record_id'], ['citation_records.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('citation_record_id', 'style', 'locale', 'renderer_version', name='uq_citation_renders_quad')
    )
    op.create_index('ix_citation_renders_citation_record_id', 'citation_renders', ['citation_record_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_citation_renders_citation_record_id', table_name='citation_renders')
    op.drop_table('citation_renders')
    op.drop_index('ix_citation_records_source_format', table_name='citation_records')
    op.drop_index('ix_citation_records_deleted_at', table_name='citation_records')
    op.drop_index('ix_citation_records_is_selected', table_name='citation_records')
    op.drop_index('ix_citation_records_paper_id', table_name='citation_records')
    op.drop_table('citation_records')
