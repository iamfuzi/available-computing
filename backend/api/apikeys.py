import secrets
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional

from database import get_session
from models import ApiKey
from api.auth import verify_token
from api.channels import _encrypt_key, _decrypt_key

router = APIRouter()


def _generate_key() -> tuple[str, str, str]:
    raw = f"ac_{secrets.token_hex(32)}"
    h = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:8]
    return raw, h, prefix


class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("")
def list_api_keys(
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    keys = session.exec(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    result = []
    for k in keys:
        raw = ""
        if k.key_encrypted:
            try:
                raw = _decrypt_key(k.key_encrypted, session)
            except Exception:
                raw = ""
        result.append({
            "id": k.id,
            "name": k.name,
            "key": raw,
            "key_prefix": k.key_prefix + "…",
            "is_active": k.is_active,
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        })
    return result


@router.post("", status_code=201)
def create_api_key(
    body: ApiKeyCreate,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    raw, h, prefix = _generate_key()
    enc = _encrypt_key(raw, session)
    key = ApiKey(name=body.name, key_hash=h, key_prefix=prefix, key_encrypted=enc)
    session.add(key)
    session.commit()
    session.refresh(key)
    return {
        "id": key.id,
        "name": key.name,
        "key": raw,
        "key_prefix": prefix + "…",
        "created_at": key.created_at.isoformat(),
    }


@router.patch("/{key_id}")
def update_api_key(
    key_id: str,
    body: ApiKeyUpdate,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    k = session.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "API key not found")
    if body.name is not None:
        k.name = body.name
    if body.is_active is not None:
        k.is_active = body.is_active
    session.add(k)
    session.commit()
    return {"ok": True}


@router.delete("/{key_id}", status_code=204)
def delete_api_key(
    key_id: str,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    k = session.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "API key not found")
    session.delete(k)
    session.commit()
