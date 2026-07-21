from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class CandidateProvider(SQLModel, table=True):
    """A community-discovered provider awaiting human review."""

    __tablename__ = "candidateprovider"

    provider_id: str = Field(primary_key=True)
    name: str
    homepage_url: str
    base_url: Optional[str] = None
    summary: str = ""
    compatibility: str = Field(default="unknown", index=True)
    access_type: str = Field(default="unknown", index=True)
    requires_card: bool = False
    admission_status: str = Field(default="review_required", index=True)
    exclusion_reason: Optional[str] = None
    model_count: int = 0
    models_json: str = "[]"
    sources_json: str = "[]"
    evidence_json: str = "{}"
    status: str = Field(default="pending", index=True)  # pending/reviewed/ignored/configured
    yaml_draft: Optional[str] = None
    is_present: bool = Field(default=True, index=True)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    last_changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CandidateSourceState(SQLModel, table=True):
    """Fetch/parser state retained so repeated source failures cannot be silent."""

    __tablename__ = "candidatesourcestate"

    source_id: str = Field(primary_key=True)
    url: str
    consecutive_failures: int = 0
    last_attempt_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_candidate_count: int = 0
    needs_attention: bool = Field(default=False, index=True)
