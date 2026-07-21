from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Notification(SQLModel, table=True):
    """Persistent, deduplicated administrator notification."""

    __tablename__ = "notification"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    dedupe_key: str = Field(unique=True, index=True)
    category: str = Field(index=True)
    severity: str = Field(default="info", index=True)
    title: str
    message: str
    action_path: Optional[str] = None
    payload_json: str = "{}"
    status: str = Field(default="unread", index=True)  # unread/read/dismissed
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow, index=True)
    read_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = Field(default=None, index=True)
