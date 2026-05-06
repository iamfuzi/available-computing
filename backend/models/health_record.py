from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class HealthRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: str = Field(foreign_key="model.id")
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    status: str
    response_ms: Optional[int] = None
    error_code: Optional[str] = None        # rate_limited / auth_failed / timeout / server_error
    is_passive: bool = False                # True = from real user call
