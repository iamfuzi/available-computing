from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field


def _utcnow():
    return datetime.now(timezone.utc)


class HealthRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: str = Field(foreign_key="model.id", ondelete="CASCADE")
    checked_at: datetime = Field(default_factory=_utcnow)
    status: str
    response_ms: Optional[int] = None
    error_code: Optional[str] = None
    is_passive: bool = False
