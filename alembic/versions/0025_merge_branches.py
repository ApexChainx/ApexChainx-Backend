"""merge independent migration branches

Revision ID: 0025_merge_branches
Revises: 0011_payment_deduplication, 0017_audit_chain, 0017_webhook_resolved_ips, 0018_webhook_secret_grace, 0019_api_keys, 0021, 0022_sla_config_history, 0023_payment_tx_indexes, 0024_audit_log_loginfail_index
Create Date: 2026-08-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0025_merge_branches"
down_revision: Union[str, None] = ('0011_payment_deduplication', '0017_audit_chain', '0017_webhook_resolved_ips', '0018_webhook_secret_grace', '0019_api_keys', '0021', '0022_sla_config_history', '0023_payment_tx_indexes', '0024_audit_log_loginfail_index')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
