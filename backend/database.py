from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event, text, inspect
from config import DATABASE_URL

# Import models so SQLModel.metadata is fully populated before create_all runs,
# regardless of import order at the call site.
import models  # noqa: F401

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _run_migrations() -> None:
    """Apply pending Alembic migrations.

    Cases:
    - ``alembic_version`` table exists with a revision → run ``upgrade head``.
    - No version table but the schema matches a known historical revision →
      stamp that revision, then upgrade. This is how pre-Alembic installations
      safely adopt new migrations without re-adding columns they already have.
    - A brand-new DB built by current ``create_all`` already matches head →
      stamp head without running ALTER statements.

    Migration failures propagate and block startup. Running with a partially
    upgraded schema is more dangerous than being temporarily unavailable.
    """
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    head_rev = ScriptDirectory.from_config(cfg).get_current_head()

    insp = inspect(engine)
    if not insp.has_table("model"):
        # No model table at all — create_all will have just built it fresh, so
        # the schema is already at head. Stamp and return.
        if head_rev:
            command.stamp(cfg, head_rev)
        return

    if insp.has_table("alembic_version"):
        # Already under Alembic control: apply any pending revisions.
        command.upgrade(cfg, "head")
        return

    def has_columns(table_name: str, columns: set[str]) -> bool:
        if not insp.has_table(table_name):
            return False
        actual = {column["name"] for column in insp.get_columns(table_name)}
        return columns.issubset(actual)

    def matches(requirements: dict[str, set[str]]) -> bool:
        return all(has_columns(table_name, columns) for table_name, columns in requirements.items())

    # A database created directly from the current SQLModel metadata is already
    # at head. Check every application table and column before stamping; a
    # handful of historical marker columns is not sufficient because
    # create_all() does not add new columns to existing tables.
    metadata_requirements = {
        table.name: {column.name for column in table.columns}
        for table in SQLModel.metadata.sorted_tables
    }
    if matches(metadata_requirements):
        if head_rev:
            command.stamp(cfg, head_rev)
        return

    baseline_model_cols = {"consecutive_billing_failures"}
    param_size_model_cols = baseline_model_cols | {"param_size"}
    cooling_model_cols = param_size_model_cols | {
        "last_success_at", "rate_limited_until", "last_429_at", "consecutive_429",
    }
    freshness_model_cols = cooling_model_cols | {
        "last_verified_at", "verification_method",
        "staleness_threshold_days", "free_expires_at",
    }
    freshness_channel_cols = {
        "status", "status_reason", "status_changed_at", "key_expires_at",
    }
    freshness_health_cols = {
        "verification_method", "http_status", "check_run_id",
        "failure_reason", "rate_limit_snapshot",
    }
    api_key_policy_cols = {
        "provider_whitelist", "provider_blacklist", "rate_limit_rpm",
        "rate_limit_rpd", "default_prefer", "default_min_context",
    }
    provenance_channel_cols = freshness_channel_cols | {
        "config_type", "discovery_source", "compliance_note",
    }

    # Pre-Alembic personal installations are identified by cumulative schema
    # markers. Pick the newest revision whose complete marker set is present,
    # stamp it, then let Alembic apply everything after it.
    known_revisions = [
        (
            "e5f6a7b8c9d0",
            {
                "model": freshness_model_cols,
                "channel": provenance_channel_cols,
                "healthrecord": freshness_health_cols,
                "apikey": api_key_policy_cols,
            },
        ),
        (
            "d4e5f6a7b8c9",
            {
                "model": freshness_model_cols,
                "channel": freshness_channel_cols,
                "healthrecord": freshness_health_cols,
                "apikey": api_key_policy_cols,
            },
        ),
        (
            "c3d4e5f6a7b8",
            {
                "model": freshness_model_cols,
                "channel": freshness_channel_cols,
                "healthrecord": freshness_health_cols,
            },
        ),
        (
            "b2c3d4e5f6a7",
            {"model": cooling_model_cols},
        ),
        ("a1b2c3d4e5f6", {"model": param_size_model_cols}),
        ("7526ef5a88ed", {"model": baseline_model_cols}),
    ]
    inferred_revision = next(
        (revision for revision, requirements in known_revisions if matches(requirements)),
        None,
    )

    if inferred_revision:
        command.stamp(cfg, inferred_revision)
    command.upgrade(cfg, "head")


def _validate_schema() -> None:
    """Fail fast when the database is missing a current model column."""
    insp = inspect(engine)
    missing: list[str] = []
    for table in SQLModel.metadata.sorted_tables:
        if not insp.has_table(table.name):
            missing.append(f"table:{table.name}")
            continue
        actual = {column["name"] for column in insp.get_columns(table.name)}
        missing.extend(
            f"{table.name}.{column.name}"
            for column in table.columns
            if column.name not in actual
        )
    if missing:
        raise RuntimeError(f"Database schema is incomplete after migrations: {', '.join(missing)}")


def create_db_and_tables():
    # Existing databases must be migrated before create_all. Otherwise a table
    # introduced by a pending migration can be pre-created at the newest
    # schema, after which the historical migration tries to add the same
    # columns again. Fresh databases are built from current metadata and then
    # stamped at head.
    existing_database = inspect(engine).has_table("model")
    if existing_database:
        _run_migrations()
    SQLModel.metadata.create_all(engine)
    if not existing_database:
        _run_migrations()

    # Ensure indexes exist for databases created before indexes were added
    with engine.connect() as conn:
        for idx_name, column in [
            ("ix_model_model_id", "model_id"),
            ("ix_model_channel_id", "channel_id"),
            ("ix_model_is_free", "is_free"),
            ("ix_model_health_status", "health_status"),
            ("ix_model_is_active", "is_active"),
        ]:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON model({column})"))
            except Exception:
                pass
        conn.commit()
    _validate_schema()


def get_session():
    with Session(engine) as session:
        yield session
