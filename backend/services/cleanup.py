from datetime import datetime, timedelta, timezone
from sqlmodel import Session, delete
from database import engine
from models import HealthRecord, RequestLog


async def cleanup_old_health_records():
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with Session(engine) as session:
        session.exec(delete(HealthRecord).where(HealthRecord.checked_at < cutoff))
        # Request logs share the same 7-day diagnostic window as health
        # records — enough for post-mortems, bounded growth otherwise.
        session.exec(delete(RequestLog).where(RequestLog.ts < cutoff))
        session.commit()
