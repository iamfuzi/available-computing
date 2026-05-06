from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
import uuid


class Model(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", ondelete="CASCADE")
    model_id: str
    display_name: Optional[str] = None
    category: Optional[str] = None          # text / vision / code / embedding
    context_length: Optional[int] = None
    rate_limit: Optional[str] = None        # JSON string
    rate_limit_source: Optional[str] = None     # manual / observed
    rate_limit_updated_at: Optional[datetime] = None
    is_free: Optional[bool] = None
    free_type: Optional[str] = None         # permanent / quota / grant / unknown
    free_source: Optional[str] = None       # provider_free / api_field / whitelist / unknown
    health_status: str = "unknown"          # healthy / slow / down / unknown
    last_response_ms: Optional[int] = None
    last_checked_at: Optional[datetime] = None
    last_real_call_at: Optional[datetime] = None
    is_active: bool = True
