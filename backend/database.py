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

    Three cases:
    - ``alembic_version`` table exists with a revision → run ``upgrade head``.
    - No ``alembic_version`` table but the ``model`` table already has every
      column the migrations would add → brand-new DB built by ``create_all``,
      stamp head (nothing to migrate).
    - No ``alembic_version`` table and the schema is behind (e.g. an old
      pre-Alembic DB missing ``consecutive_billing_failures``) → run
      ``upgrade head`` from scratch, which applies every revision.

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

    # No alembic_version table yet. Decide whether the schema is already current.
    existing_cols = {c["name"] for c in insp.get_columns("model")}
    needed_cols = {"consecutive_billing_failures", "param_size"}
    if needed_cols.issubset(existing_cols):
        # Schema is already at head (create_all built it fresh); just record it.
        if head_rev:
            command.stamp(cfg, head_rev)
    else:
        # Legacy DB behind head: run migrations from the beginning.
        try:
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
