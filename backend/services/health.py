import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

from database import engine
from models import Channel, Model, HealthRecord
from adapters import get_adapter
from config import (
    PROBE_INTERVAL_BETWEEN_MODELS_SEC,
    PROBE_GLOBAL_CONCURRENCY,
    HEARTBEAT_IDLE_DAYS,
    HEARTBEAT_MIN_PROVIDER_RPD,
    HEARTBEAT_BUDGET_RATIO,
    HEARTBEAT_REAL_TRAFFIC_RESERVE_RATIO,
)


def _set_channel_status(
    session: Session,
    channel_id: str,
    status: str,
    reason: str | None = None,
) -> None:
    """Update the channel-level credential/account state when evidence is clear."""
    channel = session.get(Channel, channel_id)
    if not channel:
        return
    if channel.status != status or channel.status_reason != reason:
        channel.status = status
        channel.status_reason = reason
        channel.status_changed_at = datetime.now(timezone.utc)
        session.add(channel)
    # Keep the channel-level alert in the same transaction as the state that
    # triggered it. A successful recovery resolves the active notification.
    from services.notifications import sync_channel_notification
    sync_channel_notification(session, channel)


async def recover_expired_cooldowns():
    """Restore models whose rate-limit cooldown has expired.

    A model marked ``rate_limited`` carries a ``rate_limited_until`` timestamp.
    Once that timestamp passes, the model is callable again, but nothing reset
    its ``health_status`` — it stayed ``rate_limited`` forever until the next
    probe sweep happened to reach it. This flips expired cooldowns back to
    ``unknown`` so the next probe re-evaluates them promptly. Run frequently
    (every few minutes) so recovered models don't linger as "limited".
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        expired = session.exec(
            select(Model).where(Model.health_status == "rate_limited")
        ).all()
        restored = 0
        for m in expired:
            until = m.rate_limited_until
            if until is None:
                # rate_limited with no cooldown end — treat as expired too,
                # since we have no basis to keep it frozen.
                pass
            elif until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until is not None and until > now:
                continue
            m.health_status = "unknown"
            m.rate_limited_until = None
            session.add(m)
            restored += 1
        if restored:
            session.commit()
        return restored


async def record_passive_health(
    model_id: str,
    response_ms: int,
    error_code: str | None,
    channel_id: str,
    decrypted_key: str,
):
    from config import SLOW_RESPONSE_THRESHOLD_MS
    if error_code:
        status = "down"
    elif response_ms >= SLOW_RESPONSE_THRESHOLD_MS:
        status = "slow"
    else:
        status = "healthy"

    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        record = HealthRecord(
            model_id=model_id,
            status=status,
            response_ms=response_ms,
            error_code=error_code,
            is_passive=True,
            verification_method="passive",
            http_status=200 if error_code is None else None,
            check_run_id=str(uuid.uuid4()),
            failure_reason=error_code,
        )
        session.add(record)

        m = session.get(Model, model_id)
        if m:
            m.health_status = status
            m.last_response_ms = response_ms
            m.last_checked_at = now
            m.last_real_call_at = now
            if error_code is None:
                m.last_success_at = now
                m.last_verified_at = now
                m.verification_method = "passive"
                m.consecutive_429 = 0
                m.rate_limited_until = None
            session.add(m)

        if error_code is None:
            _set_channel_status(session, channel_id, "active")

        session.commit()
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()


def record_rate_limit(
    model_id: str,
    retry_after_seconds: int | None,
    session: Session,
    response_ms: int | None = None,
    verification_method: str = "passive",
    check_run_id: str | None = None,
) -> int:
    """Put a model into a short cooldown after an upstream 429.

    If the provider did not send Retry-After, use a small exponential backoff so
    repeated requests do not keep selecting the same exhausted free-tier model.
    Returns the effective cooldown in seconds for structured error responses.
    """
    now = datetime.now(timezone.utc)
    m = session.get(Model, model_id)
    if not m:
        return retry_after_seconds or 60

    m.consecutive_429 += 1
    fallback = min(900, 60 * (2 ** max(0, m.consecutive_429 - 1)))
    cooldown = retry_after_seconds if retry_after_seconds and retry_after_seconds > 0 else fallback
    session.add(HealthRecord(
        model_id=model_id,
        status="rate_limited",
        response_ms=response_ms,
        error_code="rate_limited",
        is_passive=verification_method == "passive",
        verification_method=verification_method,
        http_status=429,
        check_run_id=check_run_id or str(uuid.uuid4()),
        failure_reason="rate_limited",
    ))
    m.health_status = "rate_limited"
    m.last_429_at = now
    m.last_checked_at = now
    m.rate_limited_until = now + timedelta(seconds=cooldown)
    session.add(m)
    session.commit()
    return cooldown


def clear_rate_limit(model_id: str, session: Session) -> None:
    """Clear cooldown state after a successful upstream call."""
    m = session.get(Model, model_id)
    if not m:
        return
    changed = False
    if m.consecutive_429 != 0:
        m.consecutive_429 = 0
        changed = True
    if m.rate_limited_until is not None:
        m.rate_limited_until = None
        changed = True
    if changed:
        session.add(m)
        session.commit()


def record_billing_failure(model_id: str, status_code: int, session: Session) -> bool:
    """Record a passive 401/402/403 signal without changing free policy.

    The event-recheck state machine owns the final verdict after three
    independent failures. Returns False for backward call-site compatibility.
    """
    m = session.get(Model, model_id)
    if not m or m.is_free is not True:
        return False
    now = datetime.now(timezone.utc)
    session.add(HealthRecord(
        model_id=model_id,
        status="down",
        error_code=f"upstream_{status_code}",
        is_passive=True,
        verification_method="passive",
        http_status=status_code,
        check_run_id=str(uuid.uuid4()),
        failure_reason=f"upstream_{status_code}",
    ))
    m.health_status = "down"
    m.last_checked_at = now
    m.consecutive_billing_failures += 1
    # Passive failures are signals, not final policy verdicts. The correlated
    # event-recheck batch owns the three-failures-in-30-minutes decision.
    session.add(m)
    session.commit()
    return False


def clear_billing_failures(model_id: str, session: Session) -> None:
    """Reset the billing-failure counter on a successful call."""
    m = session.get(Model, model_id)
    if m and m.consecutive_billing_failures != 0:
        m.consecutive_billing_failures = 0
        session.add(m)
        session.commit()


def record_channel_billing_failure(channel_id: str, status_code: int, session: Session) -> None:
    """Mark all free active models in a channel down after an account-level
    billing/auth failure.

    Some providers return a valid free catalog while the account cannot run any
    model (for example, SiliconFlow code 30001: insufficient balance). Treating
    that as model-specific makes routing burn through dozens of doomed
    candidates. Active probes can restore individual models later.
    """
    now = datetime.now(timezone.utc)
    check_run_id = str(uuid.uuid4())
    models = session.exec(
        select(Model)
        .where(Model.channel_id == channel_id)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
    ).all()
    for m in models:
        m.health_status = "down"
        m.last_checked_at = now
        m.consecutive_billing_failures += 1
        session.add(HealthRecord(
            model_id=m.id,
            status="down",
            error_code=f"channel_upstream_{status_code}",
            is_passive=True,
            verification_method="passive",
            http_status=status_code,
            check_run_id=check_run_id,
            failure_reason=f"channel_upstream_{status_code}",
        ))
        session.add(m)
    channel_status = "key_invalid" if status_code == 401 else "suspended"
    _set_channel_status(session, channel_id, channel_status, f"upstream_{status_code}")
    session.commit()


async def active_probe(
    model: Model,
    decrypted_key: str,
    verification_method: str = "active_heartbeat",
    *,
    check_run_id: str | None = None,
    force: bool = False,
):
    # Skip if there was a *successful* real call within the last 4 hours — a
    # recent success means passive health tracking is already fresh. A failed
    # real call (model marked down) must NOT skip probing, otherwise the model
    # is stuck down with no chance to recover.
    if not force and model.last_real_call_at and model.health_status not in ("down",):
        lrc = model.last_real_call_at
        if lrc.tzinfo is None:
            lrc = lrc.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - lrc < timedelta(hours=4):
            return None

    from models import Channel
    with Session(engine) as session:
        channel = session.get(Channel, model.channel_id)
        if not channel:
            return None

    adapter = get_adapter(channel.provider_type)
    base_url = channel.base_url or adapter.default_base_url

    check_run_id = check_run_id or str(uuid.uuid4())
    health = await adapter.health_check(model.model_id, decrypted_key, base_url)

    with Session(engine) as session:
        if health.error_code == "rate_limited":
            record_rate_limit(
                model.id,
                None,
                session,
                health.response_ms,
                verification_method=verification_method,
                check_run_id=check_run_id,
            )
            return health

        record = HealthRecord(
            model_id=model.id,
            status=health.status,
            response_ms=health.response_ms,
            error_code=health.error_code,
            is_passive=False,
            verification_method=verification_method,
            http_status=200 if health.error_code is None else (401 if health.error_code == "auth_failed" else None),
            check_run_id=check_run_id,
            failure_reason=health.error_code,
            rate_limit_snapshot=json.dumps(health.observed_rate_limit) if health.observed_rate_limit else None,
        )
        session.add(record)

        m = session.get(Model, model.id)
        if m:
            m.health_status = health.status
            m.last_response_ms = health.response_ms
            m.last_checked_at = datetime.now(timezone.utc)
            if health.status in ("healthy", "slow") and health.error_code is None:
                # A successful probe means the model is callable. Only restore
                # is_free if the model was *downgraded by a billing failure*
                # (free_source == "passive_downgrade") — that's a recoverable
                # state. Models that are is_free=None for other reasons
                # (e.g. "unknown / unconfirmed free") must NOT be auto-promoted
                # to free, because a 200 on a paid account just means the call
                # is being charged, not that the model is free.
                if m.is_free is None and m.free_source == "passive_downgrade":
                    m.is_free = True
                    m.free_type = "quota"
                    m.free_source = "probe_restored"
                m.consecutive_billing_failures = 0
                verified_at = datetime.now(timezone.utc)
                m.last_success_at = verified_at
                m.last_verified_at = verified_at
                m.verification_method = verification_method
                m.consecutive_429 = 0
                m.rate_limited_until = None
            # Update observed rate limits from response headers (always overwrites)
            if health.observed_rate_limit:
                import json as _json
                existing_rl = {}
                if m.rate_limit:
                    try:
                        existing_rl = _json.loads(m.rate_limit)
                    except Exception:
                        pass
                existing_rl.update(health.observed_rate_limit)
                m.rate_limit = _json.dumps(existing_rl)
                m.rate_limit_source = "observed"
                m.rate_limit_updated_at = datetime.now(timezone.utc)
            session.add(m)

        if health.error_code is None and health.status in ("healthy", "slow"):
            _set_channel_status(session, model.channel_id, "active")
        elif health.error_code == "auth_failed" and adapter.requires_api_key:
            _set_channel_status(session, model.channel_id, "key_invalid", "active_probe_auth_failed")

        session.commit()
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()
    return health


def _get_daily_limit(model: Model) -> int | None:
    if not model.rate_limit:
        return None
    try:
        rl = json.loads(model.rate_limit)
        rpd = rl.get("rpd")
        return rpd if isinstance(rpd, int) and rpd > 0 else None
    except Exception:
        return None


def _heartbeat_anchor(model: Model) -> datetime | None:
    values = [value for value in (model.last_real_call_at, model.last_verified_at) if value]
    if not values:
        return None
    normalized = [value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value for value in values]
    return max(normalized)


def _is_heartbeat_candidate(model: Model, now: datetime) -> bool:
    anchor = _heartbeat_anchor(model)
    return anchor is None or now - anchor >= timedelta(days=HEARTBEAT_IDLE_DAYS)


def _channel_heartbeat_budget(models: list[Model]) -> int:
    """Return the conservative daily heartbeat allowance for one credential."""
    rpds = [limit for model in models if (limit := _get_daily_limit(model)) is not None]
    if not rpds:
        return 0
    provider_rpd = min(rpds)
    if provider_rpd < HEARTBEAT_MIN_PROVIDER_RPD:
        return 0
    ratio_budget = max(1, int(provider_rpd * HEARTBEAT_BUDGET_RATIO))
    reserve_cap = max(0, int(provider_rpd * (1 - HEARTBEAT_REAL_TRAFFIC_RESERVE_RATIO)))
    return min(ratio_budget, reserve_cap)


def _heartbeat_count_today(session: Session, model_ids: list[str], now: datetime) -> int:
    if not model_ids:
        return 0
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return len(session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id.in_(model_ids))
        .where(HealthRecord.verification_method == "active_heartbeat")
        .where(HealthRecord.checked_at >= day_start)
    ).all())


async def probe_all_stale_models(get_key_fn=None):
    """Heartbeat only long-idle models within each credential's RPD budget.

    A missing RPD is not guessed. RPD below the configured safety threshold is
    also skipped, leaving those providers to passive traffic, baseline checks,
    and event-triggered rechecks.
    """
    now = datetime.now(timezone.utc)

    # Batch load all channels and decrypt keys in a single session.
    with Session(engine) as session:
        stale_models = session.exec(
            select(Model)
            .where(Model.is_active == True)
            .where(Model.is_free == True)
        ).all()

        channels = {ch.id: ch for ch in session.exec(select(Channel)).all()}
        from services.crypto import decrypt as _decrypt
        from api.channels import _get_salt
        from config import get_admin_password
        salt = _get_salt(session)

        by_channel_models: dict[str, list[Model]] = {}
        for m in stale_models:
            ch = channels.get(m.channel_id)
            if not ch or not ch.enabled or ch.status in ("key_invalid", "key_expired"):
                continue
            by_channel_models.setdefault(m.channel_id, []).append(m)

        by_channel: dict[str, list[tuple[Model, str]]] = {}
        for channel_id, all_models in by_channel_models.items():
            budget = _channel_heartbeat_budget(all_models)
            if budget <= 0:
                continue
            used = _heartbeat_count_today(session, [m.id for m in all_models], now)
            remaining = max(0, budget - used)
            if remaining <= 0:
                continue
            candidates = [m for m in all_models if _is_heartbeat_candidate(m, now)]
            candidates.sort(key=lambda m: _heartbeat_anchor(m) or datetime.min.replace(tzinfo=timezone.utc))
            ch = channels[channel_id]
            key = _decrypt(ch.api_key_enc, get_admin_password(), salt)
            by_channel[channel_id] = [(m, key) for m in candidates[:remaining]]

    async def probe_channel_sequential(items: list[tuple[Model, str]]):
        """Probe one channel's models sequentially with a delay between each.

        The delay spaces requests so a single provider doesn't see a burst
        large enough to trip its rate limiter during a probe sweep.
        """
        for index, (m, key) in enumerate(items):
            try:
                await active_probe(m, key, "active_heartbeat")
            except Exception:
                logger.exception("Active probe failed for model %s", m.model_id)
            if index < len(items) - 1:
                await asyncio.sleep(PROBE_INTERVAL_BETWEEN_MODELS_SEC)

    # Run channels concurrently, but keep a global ceiling so adding many
    # credentials cannot turn the heartbeat sweep into a traffic spike.
    channel_semaphore = asyncio.Semaphore(PROBE_GLOBAL_CONCURRENCY)

    async def bounded_channel(items: list[tuple[Model, str]]):
        async with channel_semaphore:
            await probe_channel_sequential(items)

    await asyncio.gather(
        *[bounded_channel(items) for items in by_channel.values()],
        return_exceptions=True,
    )


async def probe_channel_models(
    channel_id: str,
    verification_method: str = "active_baseline",
    only_unverified: bool = False,
):
    """Active-probe all active free models for a single channel."""
    from models import Channel

    with Session(engine) as session:
        channel = session.get(Channel, channel_id)
        if not channel or not channel.enabled:
            return

        stmt = (
            select(Model)
            .where(Model.channel_id == channel_id)
            .where(Model.is_active == True)
            .where(Model.is_free == True)
        )
        if only_unverified:
            stmt = stmt.where(Model.last_verified_at == None)
        models = session.exec(stmt).all()

        from services.crypto import decrypt as _decrypt
        from api.channels import _get_salt
        from config import get_admin_password
        salt = _get_salt(session)
        key = _decrypt(channel.api_key_enc, get_admin_password(), salt)

    # A single channel must never receive a baseline/manual burst. Different
    # channels may still be discovered concurrently by discover_all_channels.
    for index, model in enumerate(models):
        await active_probe(
            model,
            key,
            verification_method,
            force=verification_method != "active_heartbeat",
        )
        if index < len(models) - 1:
            await asyncio.sleep(PROBE_INTERVAL_BETWEEN_MODELS_SEC)
