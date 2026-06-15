"""Alembic migration environment for the available-computing backend.

Wires Alembic to the project's own ``DATABASE_URL`` (``config.py``) and the
SQLModel metadata so ``alembic revision --autogenerate`` can diff the models.
SQLite needs batch mode for ALTER TABLE, so render_as_batch is enabled.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Project imports — backend/ is on sys.path via alembic.ini prepend_sys_path.
from config import DATABASE_URL
from sqlmodel import SQLModel
import models  # noqa: F401  — import all models so metadata is fully populated

config = context.config
# Override the sqlalchemy.url from alembic.ini with the project's actual URL.
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
