from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# Import all ORM models so Alembic autogenerate sees the full metadata. Every
# module under app/models/orm/ plus the composite-model modules must appear
# here; a missing import makes autogenerate emit spurious drop_table()
# operations for tables that still exist in the database.
import app.models.job  # noqa: F401
import app.models.sla_dispute  # noqa: F401
import app.models.webhook  # noqa: F401
import app.models.orm.api_key  # noqa: F401
import app.models.orm.audit_log  # noqa: F401
import app.models.orm.outage  # noqa: F401
import app.models.orm.outage_event  # noqa: F401
import app.models.orm.payment  # noqa: F401
import app.models.orm.session  # noqa: F401
import app.models.orm.sla  # noqa: F401
import app.models.orm.sla_config_history  # noqa: F401
import app.models.orm.sla_snapshot  # noqa: F401
import app.models.orm.token_family  # noqa: F401
import app.models.orm.user  # noqa: F401
import app.models.orm.wallet  # noqa: F401
from alembic import context
from app.core.config import settings
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
