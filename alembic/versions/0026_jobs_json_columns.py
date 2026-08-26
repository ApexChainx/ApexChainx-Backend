# raw-sql-allowed
"""Convert jobs.payload and jobs.result to JSONB with validation of existing rows.

Revision ID: 0026_jobs_json_columns
Revises: 0025_merge_branches
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_jobs_json_columns"
down_revision = "0025_merge_branches"
depends_on = None
branch_labels = None


def upgrade() -> None:
    # Validate existing rows first: null out corrupt JSON values so the type
    # cast below cannot fail. Corrupt values are surfaced (logged, null) at read time.
    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN SELECT id, payload, result FROM jobs
                     WHERE payload IS NOT NULL OR result IS NOT NULL
            LOOP
                IF r.payload IS NOT NULL THEN
                    BEGIN
                        PERFORM r.payload::jsonb;
                    EXCEPTION WHEN others THEN
                        UPDATE jobs SET payload = NULL WHERE id = r.id;
                    END;
                END IF;
                IF r.result IS NOT NULL THEN
                    BEGIN
                        PERFORM r.result::jsonb;
                    EXCEPTION WHEN others THEN
                        UPDATE jobs SET result = NULL WHERE id = r.id;
                    END;
                END IF;
            END LOOP;
        END $$;
        """
    )
    op.alter_column(
        "jobs",
        "payload",
        type_=JSONB(),
        postgresql_using="payload::jsonb",
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "jobs",
        "result",
        type_=JSONB(),
        postgresql_using="result::jsonb",
        existing_type=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "jobs",
        "payload",
        type_=sa.Text(),
        postgresql_using="payload::text",
        existing_type=JSONB(),
        existing_nullable=True,
    )
    op.alter_column(
        "jobs",
        "result",
        type_=sa.Text(),
        postgresql_using="result::text",
        existing_type=JSONB(),
        existing_nullable=True,
    )
