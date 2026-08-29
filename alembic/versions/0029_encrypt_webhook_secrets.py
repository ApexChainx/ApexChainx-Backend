"""Encrypt webhook signing secrets at rest (issue #266).

Widens webhooks.secret to TEXT (Fernet tokens are longer than the old
varchar(255)) and rewrites existing plaintext secrets to ciphertext using
the same WEBHOOK_SECRET_ENCRYPTION_KEY the app uses. The rewrite is
idempotent: values that are already Fernet tokens (prefix ``gAAAAA``) are
left untouched.

Revision ID: 0029_encrypt_webhook_secrets
Revises: 0028_outage_search_trgm
Create Date: 2026-08-27
"""
import sqlalchemy as sa

from alembic import op
from app.services.secret_encryption import decrypt_secret, encrypt_secret, is_encrypted

revision = "0029_encrypt_webhook_secrets"
down_revision = "0028_outage_search_trgm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "webhooks",
        "secret",
        existing_type=sa.String(255),
        type_=sa.Text(),
        existing_nullable=True,
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, secret FROM webhooks WHERE secret IS NOT NULL")
    ).fetchall()
    for webhook_id, secret in rows:
        if secret and not is_encrypted(secret):
            encrypted = encrypt_secret(secret)
            connection.execute(
                sa.text("UPDATE webhooks SET secret = :enc WHERE id = :id"),
                {"enc": encrypted, "id": webhook_id},
            )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, secret FROM webhooks WHERE secret IS NOT NULL")
    ).fetchall()
    for webhook_id, secret in rows:
        if secret and is_encrypted(secret):
            plaintext = decrypt_secret(secret)
            connection.execute(
                sa.text("UPDATE webhooks SET secret = :plain WHERE id = :id"),
                {"plain": plaintext, "id": webhook_id},
            )

    op.alter_column(
        "webhooks",
        "secret",
        existing_type=sa.Text(),
        type_=sa.String(255),
        existing_nullable=True,
    )
