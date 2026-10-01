import secrets
import hashlib
import json
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
    # Routing profiles this key may use. Empty = all profiles allowed (the
    # personal-deployment default); non-empty is an explicit allowlist.
    allowed_profiles: list[str] = Field(default_factory=list)

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
    allowed_profiles: Optional[list[str]] = None


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
        "allowed_profiles": _json_list(key.allowed_profiles),
    }


@router.get("")
def list_api_keys(
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    """使用方清单：每把 Key 的策略 + 真实用量（今日/近 7 天，来自
    keyusageday 聚合）。用过就带着记录留在列表里，便于回答"谁在调用
    AC、调了多少、健康度如何"。"""
    from datetime import datetime, timedelta, timezone as _tz
    from models import KeyUsageDay

    keys = session.exec(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    now = datetime.now(_tz.utc)
    day_today = now.strftime("%Y-%m-%d")
    since7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usage_rows = session.exec(
        select(KeyUsageDay).where(KeyUsageDay.day >= since7)
    ).all()

    usage_by_key: dict = {}
    for row in usage_rows:
        agg = usage_by_key.setdefault(row.api_key_id, {
            "today_total": 0, "today_success": 0,
            "total_7d": 0, "success_7d": 0, "categories": set(),
        })
        agg["categories"].add(row.category)
        if row.day == day_today:
            agg["today_total"] += row.count
            if row.outcome == "success":
                agg["today_success"] += row.count
        agg["total_7d"] += row.count
        if row.outcome == "success":
            agg["success_7d"] += row.count

    result = []
    for k in keys:
        raw = ""
        if k.key_encrypted:
            try:
                raw = _decrypt_key(k.key_encrypted, session)
            except Exception:
                raw = ""
        agg = usage_by_key.get(k.id)
        usage = None
        if agg:
            usage = {
                "today_total": agg["today_total"],
                "today_success": agg["today_success"],
                "total_7d": agg["total_7d"],
                "success_rate_7d": (
                    round(agg["success_7d"] / agg["total_7d"], 4)
                    if agg["total_7d"] else None
                ),
                "categories": sorted(agg["categories"]),
                "ever_used": True,
            }
        else:
            usage = {"ever_used": False}
        result.append({
            "id": k.id,
            "name": k.name,
            "key": raw,
            "key_prefix": k.key_prefix + "…",
            "is_active": k.is_active,
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "usage": usage,
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
        allowed_profiles=json.dumps(body.allowed_profiles) if body.allowed_profiles else None,
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
    if body.allowed_profiles is not None:
        # Empty list = "all profiles allowed" (clears the allowlist). Stored
        # as NULL so is_profile_authorized treats it as the open default.
        k.allowed_profiles = json.dumps(body.allowed_profiles) if body.allowed_profiles else None
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


@router.get("/usage")
def key_usage_summary(
    days: int = 7,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    """Per-API-key daily usage summary from the keyusageday table.

    Returns one row per (day, key, category, outcome) for the last ``days``
    days plus per-key totals, so anomalous volumes (e.g. a client hammering
    /v1/embeddings) are visible at a glance. ``pending`` carries the
    not-yet-flushed in-process counters.
    """
    from datetime import datetime, timedelta, timezone

    from models import KeyUsageDay
    from services.usage import pending_count

    days = max(1, min(90, days))
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime(
        "%Y-%m-%d"
    )
    rows = session.exec(
        select(KeyUsageDay)
        .where(KeyUsageDay.day >= since)
        .order_by(KeyUsageDay.day.desc(), KeyUsageDay.api_key_id)
    ).all()

    key_names = {k.id: k.name for k in session.exec(select(ApiKey)).all()}
    totals: dict = {}
    detail = []
    for row in rows:
        detail.append(
            {
                "day": row.day,
                "api_key_id": row.api_key_id,
                "key_name": key_names.get(row.api_key_id),
                "category": row.category,
                "outcome": row.outcome,
                "count": row.count,
            }
        )
        bucket = totals.setdefault(
            row.api_key_id,
            {
                "api_key_id": row.api_key_id,
                "key_name": key_names.get(row.api_key_id),
                "success": 0,
                "fail": 0,
                "rejected_local": 0,
            },
        )
        bucket[row.outcome] = bucket.get(row.outcome, 0) + row.count

    return {
        "days": days,
        "totals": list(totals.values()),
        "detail": detail,
        "pending_flush": pending_count(),
    }
