from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select, delete
from database import engine
from models import HealthRecord


async def cleanup_old_health_records():
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with Session(engine) as session:
        session.exec(delete(HealthRecord).where(HealthRecord.checked_at < cutoff))
        session.commit()
