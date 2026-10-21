"""Merge the open migration branches into a single head.

The chain grew five parallel heads (0021_delivery_claim_auth_ledger,
0026_jobs_json_columns, 0026c_sla_payment_amounts_bigint,
0029_sla_config_publish_state, 0030_webhook_soft_delete), which makes
``alembic upgrade head`` fail with "Multiple head revisions are present".
Like 0025_merge_branches, this revision is a no-op whose only job is to
join the branches so deployments and the migration round-trip tests can
address a single head.

Revision ID: 0031_merge_open_heads
Revises: 0021_delivery_claim_auth_ledger, 0026_jobs_json_columns, 0026c_sla_payment_amounts_bigint, 0029_sla_config_publish_state, 0030_webhook_soft_delete
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0031_merge_open_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0021_delivery_claim_auth_ledger",
    "0026_jobs_json_columns",
    "0026c_sla_payment_amounts_bigint",
    "0029_sla_config_publish_state",
    "0030_webhook_soft_delete",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
