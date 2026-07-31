"""BE-010: Add audit log correlation and actor attribution

Revision ID: 0015_audit_correlation
Revises: 0014_job_retry_tracking
Create Date: 2026-04-29

Adds actor_id and correlation_id columns to audit_logs table to enable
cross-cutting correlation and consistent actor context in audit events.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '0015_audit_correlation'
down_revision = '0014_job_retry_tracking'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add correlation and actor attribution columns to audit_logs table
    op.add_column('audit_logs', sa.Column('actor_id', sa.String(255), nullable=True))
    op.add_column('audit_logs', sa.Column('correlation_id', sa.String(255), nullable=True))
    
    # Create indexes for efficient querying
    op.create_index('ix_audit_logs_actor_id', 'audit_logs', ['actor_id'])
    op.create_index('ix_audit_logs_correlation_id', 'audit_logs', ['correlation_id'])


def downgrade() -> None:
    # Remove indexes
    op.drop_index('ix_audit_logs_correlation_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_id', table_name='audit_logs')
    
    # Remove correlation and actor attribution columns
    op.drop_column('audit_logs', 'correlation_id')
    op.drop_column('audit_logs', 'actor_id')
