import base64
import threading
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlmodel import Session, select
from pydantic import BaseModel

from database import get_session
from models import Channel, Model
from adapters import get_adapter, list_providers
from adapters.declarative import DeclarativeAdapter
from services.crypto import encrypt, decrypt, generate_salt
from services.discovery import discover_channel
from api.auth import verify_token
from config import get_admin_password

router = APIRouter()

_salt_lock = threading.Lock()


def _get_salt(session: Session) -> bytes:
    with _salt_lock:
        from models import Setting
        setting = session.get(Setting, "crypto_salt")
        if not setting:
            salt = generate_salt()
            session.add(Setting(key="crypto_salt", value=base64.b64encode(salt).decode()))
            session.commit()
            return salt
        return base64.b64decode(setting.value)


def _encrypt_key(key: str, session: Session) -> str:
    salt = _get_salt(session)
    return encrypt(key, get_admin_password(), salt)


def _decrypt_key(enc: str, session: Session) -> str:
    salt = _get_salt(session)
    return decrypt(enc, get_admin_password(), salt)


class ChannelCreate(BaseModel):
    provider_type: str
    name: Optional[str] = None
    api_key: str = ""
    base_url: Optional[str] = None


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    enabled: Optional[bool] = None
    api_key: Optional[str] = None


@router.get("/providers")
def get_providers():
    return list_providers()


@router.get("")
def list_channels(session: Session = Depends(get_session), _=Depends(verify_token)):
    channels = session.exec(select(Channel)).all()
    result = []
    for ch in channels:
        decrypted_key = _decrypt_key(ch.api_key_enc, session)
        free_count = session.exec(
            select(Model)
            .where(Model.channel_id == ch.id)
            .where(Model.is_free == True)
            .where(Model.is_active == True)
        ).all()
        result.append({
            **ch.model_dump(),
            "api_key_hint": (
                "••••" + decrypted_key[-4:]
                if decrypted_key
                else "无需 Key"
            ),
            "free_model_count": len(free_count),
        })
    return result


@router.post("", status_code=201)
async def create_channel(
    body: ChannelCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    # Validate the adapter exists
    try:
        adapter = get_adapter(body.provider_type)
    except ValueError:
        raise HTTPException(400, detail=f"Unknown provider: {body.provider_type}")

    anonymous_allowed = (
        isinstance(adapter, DeclarativeAdapter)
        and adapter.config.auth.type == "none"
    )
    if not body.api_key and not anonymous_allowed:
        raise HTTPException(400, detail="API key is required for this provider")

    base_url = body.base_url or adapter.default_base_url

    # Synchronous key validation
    try:
        await adapter.validate_key(body.api_key, base_url)
    except Exception as e:
        raise HTTPException(400, detail=f"Key validation failed: {e}")

    enc_key = _encrypt_key(body.api_key, session)
    channel = Channel(
        provider_type=body.provider_type,
        name=body.name or adapter.display_name,
        api_key_enc=enc_key,
        base_url=body.base_url,
        config_type="declarative" if isinstance(adapter, DeclarativeAdapter) else "custom_adapter",
        discovery_source="manual",
        compliance_note=json.dumps(
            next(
                (item.get("compliance", {}) for item in list_providers() if item["id"] == body.provider_type),
                {},
            ),
            ensure_ascii=False,
        ),
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)

    # Async discovery — don't block the response
    background_tasks.add_task(discover_channel, channel.id)

    return {
        **channel.model_dump(),
        "api_key_hint": "••••" + body.api_key[-4:] if body.api_key else "无需 Key",
    }


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: str,
    body: ChannelUpdate,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    ch = session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404)
    if body.name is not None:
        ch.name = body.name
    if body.base_url is not None:
        ch.base_url = body.base_url
    if body.enabled is not None:
        ch.enabled = body.enabled
    if body.api_key is not None:
        ch.api_key_enc = _encrypt_key(body.api_key, session)
        ch.status = "active"
        ch.status_reason = None
        ch.status_changed_at = datetime.now(timezone.utc)
        from services.notifications import sync_channel_notification
        sync_channel_notification(session, ch)
    session.add(ch)
    session.commit()
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()
    return ch


@router.delete("/{channel_id}", status_code=204)
def delete_channel(
    channel_id: str,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    ch = session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404)
    # CASCADE DELETE on foreign keys handles models and health records
    session.delete(ch)
    session.commit()


@router.post("/{channel_id}/probe", status_code=202)
async def probe_channel(
    channel_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    ch = session.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404)
    decrypted = _decrypt_key(ch.api_key_enc, session)
    background_tasks.add_task(discover_channel, channel_id, decrypted, "manual", False)
    return {"status": "probing"}
