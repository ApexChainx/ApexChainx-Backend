from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models.job
import app.models.orm.outage
import app.models.orm.outage_event
import app.models.orm.payment
import app.models.orm.sla
import app.models.sla_dispute
import app.models.webhook  # noqa: F401
from alembic import context
from app.core.config import settings

# Import all ORM models so Alembic can detect them
from app.db.base import Base

config = context.config

# Override sqlalchemy.url with value from app settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
