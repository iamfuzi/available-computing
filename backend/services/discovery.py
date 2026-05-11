import json
import asyncio
from datetime import datetime, timezone
from sqlmodel import Session, select

from database import engine
from models import Channel, Model
from adapters import get_adapter, ModelInfo
from services.whitelist import whitelist
from services import events


def _determine_free(model: ModelInfo, provider_id: str, adapter) -> dict:
    # Step 1: whole-provider free flag
    if whitelist.is_provider_all_free(provider_id):
        return {
            "is_free": True,
            "free_type": whitelist.get_provider_free_type(provider_id),
            "free_source": "provider_free",
        }

    # Step 2: API response fields
    result = adapter.detect_free_from_api(model)
    if result:
        return {
            "is_free": result["is_free"],
            "free_type": result.get("free_type", "permanent"),
            "free_source": "api_field",
        }

    # Step 3: whitelist
    entry = whitelist.match(provider_id, model.model_id)
    if entry:
        return {
            "is_free": True,
            "free_type": entry.free_type,
            "free_source": "whitelist",
        }

    # Step 4: unknown — do NOT probe
    return {"is_free": None, "free_type": "unknown", "free_source": "unknown"}


async def discover_channel(channel_id: str, decrypted_key: str | None = None):
    with Session(engine) as session:
        channel = session.get(Channel, channel_id)
        if not channel:
            return
        # Decrypt key from DB if not provided
        if decrypted_key is None:
            from services.crypto import decrypt as _decrypt
            from api.channels import _get_salt
            import base64
            from config import get_admin_password
            salt = _get_salt(session)
            decrypted_key = _decrypt(channel.api_key_enc, get_admin_password(), salt)

    adapter = get_adapter(channel.provider_type)
    base_url = channel.base_url or adapter.default_base_url

    raw_models = await adapter.list_models(decrypted_key, base_url)

    with Session(engine) as session:
        # Mark all existing models for this channel as potentially inactive
        existing: dict[str, Model] = {
            m.model_id: m
            for m in session.exec(select(Model).where(Model.channel_id == channel_id)).all()
        }

        for raw in raw_models:
            free_info = _determine_free(raw, channel.provider_type, adapter)

            # Whitelist may supply category override
            wl_entry = whitelist.match(channel.provider_type, raw.model_id)
            category = (wl_entry.category if wl_entry and wl_entry.category else None) or raw.category

            rate_limit_json = json.dumps(raw.rate_limit) if raw.rate_limit else None
            rate_limit_source = None
            # Merge whitelist rate_limit if API didn't provide one
            if not rate_limit_json and wl_entry and wl_entry.rate_limit:
                rate_limit_json = json.dumps(wl_entry.rate_limit)
                rate_limit_source = "manual"

            if raw.model_id in existing:
                m = existing[raw.model_id]
                m.is_free = free_info["is_free"]
                m.free_type = free_info["free_type"]
                m.free_source = free_info["free_source"]
                m.category = category
                m.context_length = raw.context_length
                # Don't overwrite observed rate limits with manual ones
                if not (m.rate_limit_source == "observed" and rate_limit_source == "manual"):
                    m.rate_limit = rate_limit_json
                    if rate_limit_source:
                        m.rate_limit_source = rate_limit_source
                m.display_name = raw.display_name
                m.is_active = True
                session.add(m)
            else:
                m = Model(
                    channel_id=channel_id,
                    model_id=raw.model_id,
                    display_name=raw.display_name,
                    category=category,
                    context_length=raw.context_length,
                    rate_limit=rate_limit_json,
                    rate_limit_source=rate_limit_source,
                    is_free=free_info["is_free"],
                    free_type=free_info["free_type"],
                    free_source=free_info["free_source"],
                    is_active=True,
                )
                session.add(m)

        # Mark models not in current list as inactive (may have been removed by provider)
        current_ids = {m.model_id for m in raw_models}
        for model_id, m in existing.items():
            if model_id not in current_ids:
                m.is_active = False
                session.add(m)

        ch = session.get(Channel, channel_id)
        if ch:
            ch.last_probed_at = datetime.now(timezone.utc)
            session.add(ch)
        session.commit()

    await events.broadcast("pool_updated", {"channel_id": channel_id})

    # Health-probe newly discovered models so status isn't "unknown"
    from services.health import probe_channel_models
    await probe_channel_models(channel_id)


async def discover_all_channels(get_key_fn=None):
    """Probe all enabled channels. get_key_fn is deprecated — keys are decrypted from DB."""
    with Session(engine) as session:
        channels = session.exec(select(Channel).where(Channel.enabled == True)).all()

    semaphore = asyncio.Semaphore(5)

    async def bounded(ch):
        async with semaphore:
            await discover_channel(ch.id)

    await asyncio.gather(*[bounded(ch) for ch in channels], return_exceptions=True)
