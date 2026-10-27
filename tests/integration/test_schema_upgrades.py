"""Regression checks for bootstrap revisions and non-destructive upgrades."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from linkedin_automation.infrastructure.persistence import SqliteResearchRepository


def migration_config(database: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("LINKEDIN_AUTOMATION_MIGRATION_URL", f"sqlite:///{database.as_posix()}")
    return Config("alembic.ini")


def test_bootstrap_then_alembic_upgrade_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "bootstrap.sqlite3"
    repository = SqliteResearchRepository(database)
    repository.initialize()
    repository.close()
    command.upgrade(migration_config(database, monkeypatch), "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
    engine.dispose()


def test_repairs_only_complete_misstamped_database(tmp_path: Path):
    database = tmp_path / "complete.sqlite3"
    repository = SqliteResearchRepository(database)
    repository.initialize()
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '0003'"))
        connection.execute(text("CREATE TABLE preserved_data (value TEXT)"))
        connection.execute(text("INSERT INTO preserved_data VALUES ('keep me')"))
    repository.initialize()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        assert connection.scalar(text("SELECT value FROM preserved_data")) == "keep me"
    repository.close()
    engine.dispose()


def test_old_schema_requires_actual_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "old.sqlite3"
    config = migration_config(database, monkeypatch)
    command.upgrade(config, "0001")
    repository = SqliteResearchRepository(database)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        repository.initialize()
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
    assert "about" not in {column["name"] for column in inspect(engine).get_columns("leads")}
    command.upgrade(config, "head")
    repository.initialize()
    repository.close()
    engine.dispose()
