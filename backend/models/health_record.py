from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Index
from sqlmodel import SQLModel, Field


def _utcnow():
    return datetime.now(timezone.utc)


class HealthRecord(SQLModel, table=True):
    __table_args__ = (
        Index("ix_healthrecord_model_checked_at", "model_id", "checked_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: str = Field(foreign_key="model.id", ondelete="CASCADE")
    checked_at: datetime = Field(default_factory=_utcnow)
    status: str
    response_ms: Optional[int] = None
    error_code: Optional[str] = None
    is_passive: bool = False
    verification_method: Optional[str] = None
    http_status: Optional[int] = None
    check_run_id: Optional[str] = Field(default=None, index=True)
    failure_reason: Optional[str] = None
    rate_limit_snapshot: Optional[str] = None
