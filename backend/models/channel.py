from datetime import datetime, timezone
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_probed_at: Optional[datetime] = None
    status: str = Field(default="active", index=True)
    status_reason: Optional[str] = None
    status_changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    key_expires_at: Optional[datetime] = None
    # Provider provenance and the compliance review snapshot at onboarding.
    config_type: str = Field(default="custom_adapter", index=True)
    discovery_source: str = Field(default="manual", index=True)
    compliance_note: str = Field(default="未完成合规审核")
