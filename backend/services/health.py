import asyncio
import json
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select

from database import engine
from models import Model, HealthRecord
from adapters import get_adapter
from config import PROBE_TIMEOUT_SECONDS, BILLING_FAILURE_THRESHOLD, PROBE_INTERVAL_BETWEEN_MODELS_SEC


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

    with Session(engine) as session:
        record = HealthRecord(
            model_id=model_id,
            status=status,
            response_ms=response_ms,
            error_code=error_code,
            is_passive=True,
        )
        session.add(record)

        m = session.get(Model, model_id)
        if m:
            m.health_status = status
            m.last_response_ms = response_ms
            m.last_checked_at = datetime.now(timezone.utc)
            m.last_real_call_at = datetime.now(timezone.utc)
            session.add(m)

        session.commit()


def record_billing_failure(model_id: str, status_code: int, session: Session) -> bool:
    """Record a billing/auth failure (401/403) against a free-flagged model.

    Increments ``consecutive_billing_failures``; when it reaches
    ``BILLING_FAILURE_THRESHOLD`` the model is downgraded out of the free pool
    (``is_free = None``, ``free_type = "billing_suspect"``). Returns True if a
    downgrade occurred this call.

    Only affects models currently flagged free — a model already known to be
    paid or unknown is left alone, since a 401 there carries no new signal.
    """
    m = session.get(Model, model_id)
    if not m or m.is_free is not True:
        return False
    m.consecutive_billing_failures += 1
    if m.consecutive_billing_failures >= BILLING_FAILURE_THRESHOLD:
        m.is_free = None
        m.free_type = "billing_suspect"
        m.free_source = "passive_downgrade"
    session.add(m)
    session.commit()
    return m.is_free is None


def clear_billing_failures(model_id: str, session: Session) -> None:
    """Reset the billing-failure counter on a successful call."""
    m = session.get(Model, model_id)
    if m and m.consecutive_billing_failures != 0:
        m.consecutive_billing_failures = 0
        session.add(m)
        session.commit()


async def active_probe(model: Model, decrypted_key: str):
    # Skip if there was a real call within the last 4 hours
    if model.last_real_call_at:
        if datetime.now(timezone.utc) - model.last_real_call_at < timedelta(hours=4):
            return

    # Quota protection: no more than 5% of daily limit
    daily_limit = _get_daily_limit(model)
    if daily_limit:
        with Session(engine) as session:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            probe_count = len(session.exec(
                select(HealthRecord)
                .where(HealthRecord.model_id == model.id)
                .where(HealthRecord.is_passive == False)
                .where(HealthRecord.checked_at >= today_start)
            ).all())
            if probe_count >= daily_limit * 0.05:
                return

    with Session(engine) as session:
        channel = session.exec(
            select(Model).where(Model.id == model.id)
        ).first()

    from models import Channel
    with Session(engine) as session:
        channel = session.get(Channel, model.channel_id)
        if not channel:
            return

    adapter = get_adapter(channel.provider_type)
    base_url = channel.base_url or adapter.default_base_url

    health = await adapter.health_check(model.model_id, decrypted_key, base_url)

    with Session(engine) as session:
        record = HealthRecord(
            model_id=model.id,
            status=health.status,
            response_ms=health.response_ms,
            error_code=health.error_code,
            is_passive=False,
        )
        session.add(record)

        m = session.get(Model, model.id)
        if m:
            # Don't mark down on network errors — likely transient
            if health.error_code == "network_error":
                m.last_checked_at = datetime.now(timezone.utc)
                session.add(m)
                session.commit()
                return
            m.health_status = health.status
            m.last_response_ms = health.response_ms
            m.last_checked_at = datetime.now(timezone.utc)
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

        session.commit()


def _get_daily_limit(model: Model) -> int | None:
    if not model.rate_limit:
        return None
    try:
        rl = json.loads(model.rate_limit)
        return rl.get("rpd")
    except Exception:
        return None


async def probe_all_stale_models(get_key_fn=None):
    """Active-probe all models with no real call in the past 4 hours.

    Models are grouped by channel and probed sequentially within a channel
    (with a small delay between requests), while different channels run
    concurrently. This avoids hammering a single provider with 20 simultaneous
    probes — which is what triggers 429s on rate-limited free tiers (notably
    OpenRouter's :free models, where the daily request budget is tiny).
    """
    from models import Channel

    # Batch load all channels and decrypt keys in a single session
    with Session(engine) as session:
        stale_models = session.exec(
            select(Model).where(Model.is_active == True).where(Model.is_free == True)
        ).all()

        channels = {ch.id: ch for ch in session.exec(select(Channel)).all()}
        from services.crypto import decrypt as _decrypt
        from api.channels import _get_salt
        from config import get_admin_password
        salt = _get_salt(session)

        # Group work items by channel id
        by_channel: dict[str, list[tuple[Model, str]]] = {}
        for m in stale_models:
            ch = channels.get(m.channel_id)
            if not ch or not ch.enabled:
                continue
            key = _decrypt(ch.api_key_enc, get_admin_password(), salt)
            by_channel.setdefault(m.channel_id, []).append((m, key))

    async def probe_channel_sequential(items: list[tuple[Model, str]]):
        """Probe one channel's models sequentially with a delay between each.

        The delay spaces requests so a single provider doesn't see a burst
        large enough to trip its rate limiter during a probe sweep.
        """
        for m, key in items:
            try:
                await active_probe(m, key)
            except Exception:
                pass  # active_probe handles its own errors; swallow unexpected ones
            await asyncio.sleep(PROBE_INTERVAL_BETWEEN_MODELS_SEC)

    # Run each channel's sequential probe concurrently with the others.
    await asyncio.gather(
        *[probe_channel_sequential(items) for items in by_channel.values()],
        return_exceptions=True,
    )


async def probe_channel_models(channel_id: str):
    """Active-probe all active free models for a single channel."""
    from models import Channel

    with Session(engine) as session:
        channel = session.get(Channel, channel_id)
        if not channel or not channel.enabled:
            return

        models = session.exec(
            select(Model)
            .where(Model.channel_id == channel_id)
            .where(Model.is_active == True)
            .where(Model.is_free == True)
        ).all()

        from services.crypto import decrypt as _decrypt
        from api.channels import _get_salt
        from config import get_admin_password
        salt = _get_salt(session)
        key = _decrypt(channel.api_key_enc, get_admin_password(), salt)

    semaphore = asyncio.Semaphore(10)

    async def bounded(coro):
        async with semaphore:
            return await coro

    await asyncio.gather(*[bounded(active_probe(m, key)) for m in models], return_exceptions=True)
