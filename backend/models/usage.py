"""Per-API-key daily usage aggregation.

One row per (day, api_key_id, category, outcome). Counters are bumped
in-process on every proxied request and flushed to this table periodically
(see services/usage.py), so the request path pays no extra DB write and the
data survives restarts within one flush interval.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KeyUsageDay(SQLModel, table=True):
    __tablename__ = "keyusageday"
    __table_args__ = (
        UniqueConstraint(
            "day", "api_key_id", "category", "outcome", name="uq_key_usage_day"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    # YYYY-MM-DD (UTC)
    day: str = Field(index=True)
    # ApiKey.id, or "admin" for admin-JWT calls
    api_key_id: str = Field(index=True)
    # chat | embeddings | rerank | image
    category: str
    # success | fail | rejected_local
    outcome: str
    count: int = Field(default=0)
    updated_at: datetime = Field(default_factory=_utcnow)
