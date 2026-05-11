from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event, text
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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


def get_session():
    with Session(engine) as session:
        yield session
