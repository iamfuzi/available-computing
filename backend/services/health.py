import asyncio
import json
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select

from database import engine
from models import Model, HealthRecord
from adapters import get_adapter
from config import PROBE_TIMEOUT_SECONDS


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
    """Active-probe all models with no real call in the past 4 hours."""
    with Session(engine) as session:
        stale_models = session.exec(
            select(Model).where(Model.is_active == True).where(Model.is_free == True)
        ).all()

    from models import Channel
    tasks = []
    for m in stale_models:
        with Session(engine) as session:
            ch = session.get(Channel, m.channel_id)
            if not ch or not ch.enabled:
                continue
            from services.crypto import decrypt as _decrypt
            from api.channels import _get_salt
            from config import get_admin_password
            salt = _get_salt(session)
            key = _decrypt(ch.api_key_enc, get_admin_password(), salt)
        tasks.append(active_probe(m, key))

    semaphore = asyncio.Semaphore(20)

    async def bounded(coro):
        async with semaphore:
            return await coro

    await asyncio.gather(*[bounded(t) for t in tasks], return_exceptions=True)
