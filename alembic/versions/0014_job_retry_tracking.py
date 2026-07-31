"""BE-041: Add job retry tracking columns

Revision ID: 0014_job_retry_tracking
Revises: 0013_webhook_secret_metadata
Create Date: 2026-04-29

Adds retry_count, max_retries, and last_retried_at columns to the jobs table
to support intentional job retry functionality with configurable retry policies.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '0014_job_retry_tracking'
down_revision = '0013_webhook_secret_metadata'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add retry tracking columns to jobs table
    op.add_column('jobs', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('jobs', sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('jobs', sa.Column('last_retried_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove retry tracking columns
    op.drop_column('jobs', 'last_retried_at')
    op.drop_column('jobs', 'max_retries')
    op.drop_column('jobs', 'retry_count')
