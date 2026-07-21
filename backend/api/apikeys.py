import secrets
import hashlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional

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


class KeyRateLimit(BaseModel):
    rpm: Optional[int] = Field(default=None, ge=1)
    rpd: Optional[int] = Field(default=None, ge=1)


class DefaultRoutingPolicy(BaseModel):
    prefer: Literal["latency", "capability"] = "latency"
    min_context: Optional[int] = Field(default=None, ge=1)


class ApiKeyCreate(BaseModel):
    name: str
    provider_whitelist: list[str] = Field(default_factory=list)
    provider_blacklist: list[str] = Field(default_factory=list)
    rate_limit: KeyRateLimit = Field(default_factory=KeyRateLimit)
    default_routing_policy: DefaultRoutingPolicy = Field(default_factory=DefaultRoutingPolicy)

    @model_validator(mode="after")
    def validate_provider_policy(self):
        overlap = set(self.provider_whitelist) & set(self.provider_blacklist)
        if overlap:
            raise ValueError("providers cannot be both allowed and blocked")
        return self


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    provider_whitelist: Optional[list[str]] = None
    provider_blacklist: Optional[list[str]] = None
    rate_limit: Optional[KeyRateLimit] = None
    default_routing_policy: Optional[DefaultRoutingPolicy] = None


def _json_list(value: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(value) if value else []
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _policy_dict(key: ApiKey) -> dict:
    return {
        "provider_whitelist": _json_list(key.provider_whitelist),
        "provider_blacklist": _json_list(key.provider_blacklist),
        "rate_limit": {"rpm": key.rate_limit_rpm, "rpd": key.rate_limit_rpd},
        "default_routing_policy": {
            "prefer": key.default_prefer,
            "min_context": key.default_min_context,
        },
    }


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
            **_policy_dict(k),
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
    key = ApiKey(
        name=body.name,
        key_hash=h,
        key_prefix=prefix,
        key_encrypted=enc,
        provider_whitelist=json.dumps(body.provider_whitelist),
        provider_blacklist=json.dumps(body.provider_blacklist),
        rate_limit_rpm=body.rate_limit.rpm,
        rate_limit_rpd=body.rate_limit.rpd,
        default_prefer=body.default_routing_policy.prefer,
        default_min_context=body.default_routing_policy.min_context,
    )
    session.add(key)
    session.commit()
    session.refresh(key)
    return {
        "id": key.id,
        "name": key.name,
        "key": raw,
        "key_prefix": prefix + "…",
        "is_active": key.is_active,
        "created_at": key.created_at.isoformat(),
        "last_used_at": None,
        **_policy_dict(key),
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
    if body.provider_whitelist is not None:
        k.provider_whitelist = json.dumps(body.provider_whitelist)
    if body.provider_blacklist is not None:
        k.provider_blacklist = json.dumps(body.provider_blacklist)
    if body.rate_limit is not None:
        k.rate_limit_rpm = body.rate_limit.rpm
        k.rate_limit_rpd = body.rate_limit.rpd
    if body.default_routing_policy is not None:
        k.default_prefer = body.default_routing_policy.prefer
        k.default_min_context = body.default_routing_policy.min_context
    overlap = set(_json_list(k.provider_whitelist)) & set(_json_list(k.provider_blacklist))
    if overlap:
        raise HTTPException(422, "providers cannot be both allowed and blocked")
    session.add(k)
    session.commit()
    return {"ok": True, **_policy_dict(k)}


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
