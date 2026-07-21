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

    Migration failures are logged but never block startup.
    """
    import logging
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    log = logging.getLogger("database")
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
        try:
            command.upgrade(cfg, "head")
        except Exception as e:  # pragma: no cover — best-effort
            log.warning("Alembic upgrade failed (continuing): %s", e)
        return

    # No alembic_version table yet. Infer the newest known compatible revision.
    model_cols = {c["name"] for c in insp.get_columns("model")}
    channel_cols = {c["name"] for c in insp.get_columns("channel")} if insp.has_table("channel") else set()
    health_cols = {c["name"] for c in insp.get_columns("healthrecord")} if insp.has_table("healthrecord") else set()
    apikey_cols = {c["name"] for c in insp.get_columns("apikey")} if insp.has_table("apikey") else set()

    head_model_cols = {
        "last_verified_at", "verification_method",
        "staleness_threshold_days", "free_expires_at",
    }
    head_channel_cols = {
        "status", "status_reason", "status_changed_at", "key_expires_at",
        "config_type", "discovery_source", "compliance_note",
    }
    head_health_cols = {
        "verification_method", "http_status", "check_run_id",
        "failure_reason", "rate_limit_snapshot",
    }
    head_apikey_cols = {
        "provider_whitelist", "provider_blacklist", "rate_limit_rpm",
        "rate_limit_rpd", "default_prefer", "default_min_context",
    }
    if (
        head_model_cols.issubset(model_cols)
        and head_channel_cols.issubset(channel_cols)
        and head_health_cols.issubset(health_cols)
        and head_apikey_cols.issubset(apikey_cols)
    ):
        if head_rev:
            command.stamp(cfg, head_rev)
        return

    known_revisions = [
        (
            "c3d4e5f6a7b8",
            head_model_cols,
        ),
        (
            "b2c3d4e5f6a7",
            {
                "consecutive_billing_failures", "param_size", "last_success_at",
                "rate_limited_until", "last_429_at", "consecutive_429",
            },
        ),
        ("a1b2c3d4e5f6", {"consecutive_billing_failures", "param_size"}),
        ("7526ef5a88ed", {"consecutive_billing_failures"}),
    ]
    inferred_revision = next(
        (revision for revision, columns in known_revisions if columns.issubset(model_cols)),
        None,
    )

    try:
        if inferred_revision:
            command.stamp(cfg, inferred_revision)
        command.upgrade(cfg, "head")
    except Exception as e:  # pragma: no cover — best-effort
        log.warning("Alembic upgrade failed (continuing): %s", e)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
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
    _run_migrations()


def get_session():
    with Session(engine) as session:
        yield session
