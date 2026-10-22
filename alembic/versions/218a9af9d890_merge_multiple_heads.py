"""merge multiple heads

The committed history grew four parallel heads (0026_jobs_json_columns,
0026c_sla_payment_amounts_bigint, 0029_sla_config_publish_state,
0030_webhook_soft_delete) without a merge revision, so `alembic upgrade head`
failed with "Multiple head revisions are present" and the DB-backed tests that
assume a migrated schema could never run. This revision is a pure graph merge:
it carries no schema operations of its own and exists only so `head` resolves
to a single tip again.

Revision ID: 218a9af9d890
Revises: 0026_jobs_json_columns, 0026c_sla_payment_amounts_bigint, 0029_sla_config_publish_state, 0030_webhook_soft_delete
Create Date: 2026-09-28 20:37:28.202854
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "218a9af9d890"
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
