"""Alembic migration environment.

Uses the same DATABASE_URL the app uses, and imports Base so autogenerate works.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import models for its side effect: every mapped class gets registered on
# ``Base.metadata`` before Alembic autogenerate walks the metadata tree.
# Without this, ``alembic revision --autogenerate`` produces an empty
# upgrade() for the whole schema.
import idraa.models  # noqa: F401  (side-effect import)
from idraa.config import get_settings
from idraa.db import Base

# Alembic Config object
config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: the default (True) silently disables
    # every logger that was created before alembic ran, which breaks app
    # loggers like ``idraa.middleware.session`` when migrations run in
    # the same Python process as the app (primarily the migration-smoke
    # test, but would also bite a future "run migrations on startup" flow).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Inject our DATABASE_URL into the alembic config, unless the caller has
# already overridden ``sqlalchemy.url`` programmatically (e.g. the
# migration-reversibility test points at a tmp-path SQLite file via
# ``Config.set_main_option``). The placeholder in ``alembic.ini``
# (``driver://user:pass@localhost/dbname``) is treated as unset so the
# app settings win by default.
_ALEMBIC_INI_PLACEHOLDER = "driver://user:pass@localhost/dbname"
_configured_url = config.get_main_option("sqlalchemy.url")
if not _configured_url or _configured_url == _ALEMBIC_INI_PLACEHOLDER:
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

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


def do_run_migrations(connection: Connection) -> None:
    # compare_type=False suppresses spurious VARCHAR→Enum(native_enum=False)
    # drift on SQLite: SQLite doesn't persist CHECK constraint info so Alembic
    # reflects all Enum(native_enum=False) columns back as VARCHAR, producing
    # false-positive type-change diffs on every enum column. This is a
    # SQLite-specific limitation; Postgres correctly reflects the CHECK
    # constraint name and suppresses the diff. Type safety is enforced at the
    # ORM layer (StrEnum values + Pydantic validation), not at the DB layer on
    # SQLite dev instances.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
