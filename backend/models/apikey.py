from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field
import uuid


class ApiKey(SQLModel, table=True):
    __tablename__ = "apikey"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    key_hash: str = Field(index=True)
    key_prefix: str
    key_encrypted: str = Field(default="")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: Optional[datetime] = None
