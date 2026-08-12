"""create_doi_enrichment_tables

Revision ID: c2f02a8e1b39
Revises: b1a2c3d4e5f6
Create Date: 2026-07-28 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2f02a8e1b39'
down_revision: Union[str, Sequence[str], None] = 'b1a2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('doi_enrichment_jobs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('paper_id', sa.String(length=32), nullable=False),
    sa.Column('doi', sa.String(length=256), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('paper_id')
    )
    op.create_index('ix_doi_enrichment_jobs_paper_id', 'doi_enrichment_jobs', ['paper_id'], unique=False)
    op.create_index('ix_doi_enrichment_jobs_status', 'doi_enrichment_jobs', ['status'], unique=False)
    op.create_table('doi_provider_results',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('job_id', sa.String(length=32), nullable=False),
    sa.Column('paper_id', sa.String(length=32), nullable=False),
    sa.Column('doi', sa.String(length=256), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('request_url', sa.String(length=2048), nullable=False),
    sa.Column('request_headers_json', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('raw_json', sa.Text(), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=2048), nullable=True),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['doi_enrichment_jobs.id'], ),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'provider', name='uq_doi_provider_results_job_provider')
    )
    op.create_index('ix_doi_provider_results_paper_id', 'doi_provider_results', ['paper_id'], unique=False)
    op.create_index('ix_doi_provider_results_provider', 'doi_provider_results', ['provider'], unique=False)
    op.create_index('ix_doi_provider_results_status', 'doi_provider_results', ['status'], unique=False)
    op.create_index('ix_doi_provider_results_next_retry_at', 'doi_provider_results', ['next_retry_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_doi_provider_results_next_retry_at', table_name='doi_provider_results')
    op.drop_index('ix_doi_provider_results_status', table_name='doi_provider_results')
    op.drop_index('ix_doi_provider_results_provider', table_name='doi_provider_results')
    op.drop_index('ix_doi_provider_results_paper_id', table_name='doi_provider_results')
    op.drop_table('doi_provider_results')
    op.drop_index('ix_doi_enrichment_jobs_status', table_name='doi_enrichment_jobs')
    op.drop_index('ix_doi_enrichment_jobs_paper_id', table_name='doi_enrichment_jobs')
    op.drop_table('doi_enrichment_jobs')
