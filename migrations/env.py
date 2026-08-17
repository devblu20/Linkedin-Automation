"""Alembic environment for SQLite and PostgreSQL persistence."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from linkedin_automation.infrastructure.persistence.repository import Base

config = context.config
if migration_url := os.getenv("LINKEDIN_AUTOMATION_MIGRATION_URL"):
    if migration_url.startswith("postgresql://"):
        migration_url = migration_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config.set_main_option("sqlalchemy.url", migration_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def ensure_sqlite_parent() -> None:
    """Create the configured SQLite parent directory before connecting."""
    url = make_url(config.get_main_option("sqlalchemy.url"))
    if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
        from pathlib import Path

        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    ensure_sqlite_parent()
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
