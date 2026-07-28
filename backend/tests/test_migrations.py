from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

import config
import database


HEAD_REVISION = "k1f2a3b4c5d6"


def _file_engine(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "db.sqlite"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(config, "DATABASE_URL", database_url)
    return engine


def _stamp(engine, revision: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )


def _revision(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _columns(engine, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _indexes(engine, table_name: str) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes(table_name)}


def test_fresh_database_is_created_and_stamped_at_head(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path, monkeypatch)

    database.create_db_and_tables()

    assert _revision(engine) == HEAD_REVISION
    assert "allowed_profiles" in _columns(engine, "apikey")
    assert "consecutive_errors" in _columns(engine, "model")


def test_legacy_unversioned_schema_migrates_before_create_all(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path, monkeypatch)
    SQLModel.metadata.create_all(engine)

    # Recreate the last pre-candidate-pool schema: core tables already contain
    # the e5 provenance fields, while tables/columns introduced later do not
    # exist and there is no alembic_version table.
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE candidateprovider"))
        connection.execute(text("DROP TABLE candidatesourcestate"))
        connection.execute(text("DROP TABLE notification"))
        connection.execute(text("ALTER TABLE apikey DROP COLUMN allowed_profiles"))
        connection.execute(text("ALTER TABLE model DROP COLUMN consecutive_errors"))

    database.create_db_and_tables()

    assert _revision(engine) == HEAD_REVISION
    assert "allowed_profiles" in _columns(engine, "apikey")
    assert "consecutive_errors" in _columns(engine, "model")
    assert inspect(engine).has_table("notification")
    assert {
        "access_type",
        "requires_card",
        "admission_status",
        "exclusion_reason",
    }.issubset(_columns(engine, "candidateprovider"))


def test_create_all_mixed_schema_continues_from_g7_safely(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path, monkeypatch)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        # This is the state produced by the old startup order: create_all made
        # candidateprovider at the newest schema, while existing core tables
        # still lack columns from migrations that have not run.
        connection.execute(text("ALTER TABLE apikey DROP COLUMN allowed_profiles"))
        connection.execute(text("ALTER TABLE model DROP COLUMN consecutive_errors"))
    _stamp(engine, "f6a7b8c9d0e1")

    database.create_db_and_tables()

    assert _revision(engine) == HEAD_REVISION
    assert "allowed_profiles" in _columns(engine, "apikey")
    assert "consecutive_errors" in _columns(engine, "model")
    assert "ix_candidateprovider_access_type" in _indexes(engine, "candidateprovider")
    assert "ix_candidateprovider_admission_status" in _indexes(
        engine, "candidateprovider"
    )


def test_repair_migration_fixes_incorrectly_stamped_head(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path, monkeypatch)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE apikey DROP COLUMN allowed_profiles"))
        connection.execute(text("ALTER TABLE model DROP COLUMN consecutive_errors"))
        connection.execute(text("DROP INDEX ix_candidateprovider_access_type"))
        connection.execute(text("ALTER TABLE candidateprovider DROP COLUMN exclusion_reason"))
    _stamp(engine, "j0e1f2a3b4c5")

    database.create_db_and_tables()

    assert _revision(engine) == HEAD_REVISION
    assert "allowed_profiles" in _columns(engine, "apikey")
    assert "consecutive_errors" in _columns(engine, "model")
    assert "exclusion_reason" in _columns(engine, "candidateprovider")
    assert "ix_candidateprovider_access_type" in _indexes(engine, "candidateprovider")


def test_migration_failure_blocks_startup(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path, monkeypatch)
    SQLModel.metadata.create_all(engine)
    _stamp(engine, "j0e1f2a3b4c5")

    def fail_upgrade(*args, **kwargs):
        raise RuntimeError("migration failed")

    monkeypatch.setattr("alembic.command.upgrade", fail_upgrade)

    with pytest.raises(RuntimeError, match="migration failed"):
        database.create_db_and_tables()
