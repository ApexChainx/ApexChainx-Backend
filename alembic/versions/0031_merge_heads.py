"""merge the 0026/0026c and 0029/0030 migration branches

The 0026_jobs_json_columns and 0026c_sla_payment_amounts_bigint branches were
both cut from 0025_merge_branches (via 0026b for the latter), and the
0029_sla_config_publish_state and 0030_webhook_soft_delete branches were both
cut from 0028_outage_search_trgm (via 0029_encrypt_webhook_secrets for the
latter). None of them was ever converged, leaving four heads in the graph:
`alembic upgrade head` then fails outright ("Multiple head revisions are
present") and every CI migration check with it.

This revision is a pure graph merge: no schema changes, and the downgrade
simply un-merges the graph without touching the database.

Revision ID: 0031_merge_heads
Revises: 0026_jobs_json_columns, 0026c_sla_payment_amounts_bigint, 0029_sla_config_publish_state, 0030_webhook_soft_delete
Create Date: 2026-09-27

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "0031_merge_heads"
down_revision: Union[str, None] = (
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
