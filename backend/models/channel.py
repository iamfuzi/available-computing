from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
import uuid


class Channel(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    provider_type: str
    name: str
    api_key_enc: str
    base_url: Optional[str] = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_probed_at: Optional[datetime] = None
