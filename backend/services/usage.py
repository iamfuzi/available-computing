"""In-process usage counters with periodic DB flush.

Proxy endpoints bump counters on every request (success / fail /
rejected_local); a scheduler job flushes them into the keyusageday table
once a minute. This keeps per-request cost at a dict update and makes
per-key daily volume visible in the admin API (misconfigurations like a
client hammering /v1/embeddings surface within a minute instead of days).
"""

import logging
import threading
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: Counter = Counter()

OUTCOMES = ("success", "fail", "rejected_local")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_usage(api_key_id: str, category: str, outcome: str) -> None:
    """Bump the daily counter for (key, category, outcome). Never raises."""
    try:
        with _lock:
            _counters[(_today(), api_key_id, category, outcome)] += 1
    except Exception:  # noqa: BLE001 — usage accounting must not break proxying
        logger.exception("record_usage failed")


def pending_count() -> int:
    with _lock:
        return sum(_counters.values())


def flush_usage() -> int:
    """Upsert pending counters into keyusageday; returns rows written.

    Opens its own short-lived session (safe from scheduler and tests)."""
    with _lock:
        pending = dict(_counters)
        _counters.clear()
    if not pending:
        return 0

    from sqlmodel import Session, select

    from database import engine
    from models.usage import KeyUsageDay

    written = 0
    try:
        with Session(engine) as session:
            for (day, key_id, category, outcome), delta in pending.items():
                row = session.exec(
                    select(KeyUsageDay)
                    .where(KeyUsageDay.day == day)
                    .where(KeyUsageDay.api_key_id == key_id)
                    .where(KeyUsageDay.category == category)
                    .where(KeyUsageDay.outcome == outcome)
                ).first()
                if row is None:
                    session.add(
                        KeyUsageDay(
                            day=day,
                            api_key_id=key_id,
                            category=category,
                            outcome=outcome,
                            count=delta,
                        )
                    )
                else:
                    row.count += delta
                    row.updated_at = datetime.now(timezone.utc)
                written += 1
            session.commit()
    except Exception:  # noqa: BLE001 — flush failure must not kill the job
        # Put the counters back so the next flush retries instead of losing them
        with _lock:
            for k, delta in pending.items():
                _counters[k] += delta
        logger.exception("flush_usage failed, counters retained for retry")
        return 0
    return written
