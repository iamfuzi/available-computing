"""Correlated, event-triggered model rechecks.

Passive failures schedule work outside request I/O. A single check_run_id ties
all attempts together in HealthRecord so the 30-minute decision can be audited.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from config import (
    EVENT_RECHECK_INITIAL_DELAY_SECONDS,
    EVENT_RECHECK_MAX_ATTEMPTS,
    EVENT_RECHECK_RETRY_DELAY_SECONDS,
    EVENT_RECHECK_WINDOW_MINUTES,
    get_admin_password,
)
from database import engine
from models import Channel, HealthRecord, Model

logger = logging.getLogger(__name__)

# The scheduler is in-memory in the personal single-process deployment. This
# set prevents a burst of failing requests from creating duplicate retry trees.
_pending_rechecks: dict[str, str] = {}


def trigger_event_recheck(model_id: str, trigger_reason: str) -> str:
    """Schedule one correlated recheck batch and return its check_run_id."""
    existing = _pending_rechecks.get(model_id)
    if existing:
        return existing

    check_run_id = str(uuid.uuid4())
    _pending_rechecks[model_id] = check_run_id
    window_started_at = datetime.now(timezone.utc)
    try:
        _schedule_attempt(
            model_id=model_id,
            trigger_reason=trigger_reason,
            check_run_id=check_run_id,
            attempt=1,
            window_started_at=window_started_at,
            delay_seconds=EVENT_RECHECK_INITIAL_DELAY_SECONDS,
        )
    except Exception:
        _pending_rechecks.pop(model_id, None)
        logger.exception("Failed to schedule event recheck for model %s", model_id)
    return check_run_id


def _schedule_attempt(
    *,
    model_id: str,
    trigger_reason: str,
    check_run_id: str,
    attempt: int,
    window_started_at: datetime,
    delay_seconds: int,
) -> None:
    from services.scheduler import scheduler

    scheduler.add_job(
        run_event_recheck,
        "date",
        kwargs={
            "model_id": model_id,
            "trigger_reason": trigger_reason,
            "check_run_id": check_run_id,
            "attempt": attempt,
            "window_started_at": window_started_at.isoformat(),
        },
        id=f"event_recheck:{check_run_id}:{attempt}",
        replace_existing=True,
        run_date=datetime.now(timezone.utc) + timedelta(seconds=max(0, delay_seconds)),
    )


def _record_recheck_exception(model_id: str, check_run_id: str, reason: str) -> None:
    with Session(engine) as session:
        if not session.get(Model, model_id):
            return
        session.add(HealthRecord(
            model_id=model_id,
            status="slow",
            error_code="recheck_exception",
            is_passive=False,
            verification_method="active_event_triggered",
            check_run_id=check_run_id,
            failure_reason=reason[:500],
        ))
        session.commit()


def _apply_confirmed_change(model_id: str, trigger_reason: str, check_run_id: str) -> None:
    with Session(engine) as session:
        model = session.get(Model, model_id)
        if not model:
            return
        records = session.exec(
            select(HealthRecord)
            .where(HealthRecord.model_id == model_id)
            .where(HealthRecord.check_run_id == check_run_id)
            .where(HealthRecord.verification_method == "active_event_triggered")
        ).all()
        failed_count = sum(1 for record in records if record.error_code is not None)
        if failed_count < EVENT_RECHECK_MAX_ATTEMPTS:
            return

        old_free_type = model.free_type
        old_is_free = model.is_free
        if trigger_reason == "rate_limited":
            # The model remains free but has a confirmed quota-shaped policy.
            model.free_type = "quota"
            model.free_source = "event_recheck"
            session.add(model)
        elif trigger_reason == "upstream_402":
            # Payment Required is a model/free-policy signal, not a credential
            # signal. Keep it out of automatic routing pending manual review.
            model.is_free = None
            model.free_type = "billing_suspect"
            model.free_source = "event_recheck"
            session.add(model)
        # 401/403 are credential signals. active_probe has already updated the
        # Channel to key_invalid; do not rewrite the model's free classification.
        if (model.free_type, model.is_free) != (old_free_type, old_is_free):
            from services.notifications import upsert_notification
            upsert_notification(
                session,
                dedupe_key=f"policy_change:{model.id}:{check_run_id}",
                category="policy_change",
                severity="warning",
                title=f"{model.model_id} 免费策略疑似变化",
                message=(
                    f"30 分钟窗口内独立复检失败 {failed_count} 次；"
                    f"{old_free_type or 'unknown'} → {model.free_type or 'unknown'}，请人工确认。"
                ),
                action_path=f"/models/{model.id}",
                payload={
                    "model_id": model.id,
                    "check_run_id": check_run_id,
                    "attempts": failed_count,
                    "trigger_reason": trigger_reason,
                    "old_free_type": old_free_type,
                    "new_free_type": model.free_type,
                },
            )
        session.commit()
        logger.warning(
            "Confirmed upstream policy signal for model=%s reason=%s run=%s attempts=%s",
            model_id,
            trigger_reason,
            check_run_id,
            failed_count,
        )


async def run_event_recheck(
    *,
    model_id: str,
    trigger_reason: str,
    check_run_id: str,
    attempt: int,
    window_started_at: str,
) -> None:
    """Run one attempt, then recover, retry, or persist a confirmed change."""
    from api.channels import _get_salt
    from services.crypto import decrypt
    from services.health import active_probe

    started_at = datetime.fromisoformat(window_started_at)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    with Session(engine) as session:
        model = session.get(Model, model_id)
        channel = session.get(Channel, model.channel_id) if model else None
        if not model or not channel or not channel.enabled:
            _pending_rechecks.pop(model_id, None)
            return
        key = decrypt(channel.api_key_enc, get_admin_password(), _get_salt(session))

    try:
        health = await active_probe(
            model,
            key,
            "active_event_triggered",
            check_run_id=check_run_id,
            force=True,
        )
    except Exception as exc:
        logger.exception("Event recheck failed for model %s", model_id)
        _record_recheck_exception(model_id, check_run_id, type(exc).__name__)
        health = None

    if health and health.error_code is None and health.status in ("healthy", "slow"):
        _pending_rechecks.pop(model_id, None)
        return

    now = datetime.now(timezone.utc)
    window_end = started_at + timedelta(minutes=EVENT_RECHECK_WINDOW_MINUTES)
    if attempt >= EVENT_RECHECK_MAX_ATTEMPTS:
        _apply_confirmed_change(model_id, trigger_reason, check_run_id)
        from services.notifications import broadcast_notifications_updated
        await broadcast_notifications_updated()
        _pending_rechecks.pop(model_id, None)
        return
    if now + timedelta(seconds=EVENT_RECHECK_RETRY_DELAY_SECONDS) > window_end:
        # Fewer than three checks inside the required window is inconclusive.
        _pending_rechecks.pop(model_id, None)
        return

    _schedule_attempt(
        model_id=model_id,
        trigger_reason=trigger_reason,
        check_run_id=check_run_id,
        attempt=attempt + 1,
        window_started_at=started_at,
        delay_seconds=EVENT_RECHECK_RETRY_DELAY_SECONDS,
    )
