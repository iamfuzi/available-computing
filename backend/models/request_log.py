from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Index


def _utcnow():
    return datetime.now(timezone.utc)


class RequestLog(SQLModel, table=True):
    """One row per terminal proxy request (success or failure).

    The pool previously kept only aggregated counters (keyusageday) and
    health-by-model records, so questions like "which provider failed
    yesterday 15:00-16:00" were unanswerable after docker logs rotated away
    with a rebuild. Rows are fire-and-forget diagnostics; cleanup keeps 7
    days, same window as health records.
    """

    __table_args__ = (
        Index("ix_requestlog_ts", "ts"),
        Index("ix_requestlog_outcome_ts", "outcome", "ts"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_utcnow)
    request_id: Optional[str] = Field(default=None, index=True)
    api_key_id: Optional[str] = None      # ApiKey.id, or "admin" for console tokens
    category: str                         # chat / embedding / rerank / image
    requested_model: Optional[str] = None # caller-facing model (incl. auto:*)
    selected_model: Optional[str] = None  # upstream model that served/failed
    provider: Optional[str] = None        # provider_type of the last attempt
    outcome: str                          # success / fail / rejected_local
    status_code: Optional[int] = None     # final HTTP status returned to caller
    error_code: Optional[str] = None
    latency_ms: Optional[int] = None
    attempted: Optional[str] = None       # "provider/model" chain, comma-separated
