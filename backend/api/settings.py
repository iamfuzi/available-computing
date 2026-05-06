from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from pydantic import BaseModel
from typing import Optional

from database import get_session
from models import Setting
from api.auth import verify_token
from services.whitelist import whitelist

router = APIRouter()

SETTING_KEYS = {
    "discovery_interval_hours": "6",
    "probe_interval_hours": "2",
    "slow_threshold_ms": "1000",
}


@router.get("")
def get_settings(session: Session = Depends(get_session), _=Depends(verify_token)):
    result = dict(SETTING_KEYS)
    for key in SETTING_KEYS:
        row = session.get(Setting, key)
        if row:
            result[key] = row.value
    result["whitelist_version"] = whitelist.version
    return result


class SettingsUpdate(BaseModel):
    discovery_interval_hours: Optional[int] = None
    probe_interval_hours: Optional[int] = None
    slow_threshold_ms: Optional[int] = None


@router.patch("")
def update_settings(
    body: SettingsUpdate,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    updates = body.model_dump(exclude_none=True)
    for key, val in updates.items():
        # Validate ranges
        if key == "discovery_interval_hours" and (val < 1 or val > 48):
            raise HTTPException(400, detail=f"{key} must be 1-48")
        if key == "probe_interval_hours" and (val < 1 or val > 24):
            raise HTTPException(400, detail=f"{key} must be 1-24")
        if key == "slow_threshold_ms" and (val < 100 or val > 10000):
            raise HTTPException(400, detail=f"{key} must be 100-10000")
        row = session.get(Setting, key)
        if row:
            row.value = str(val)
        else:
            row = Setting(key=key, value=str(val))
        session.add(row)
    session.commit()

    # Refresh scheduler intervals if discovery/probe hours changed
    scheduler_keys = {"discovery_interval_hours", "probe_interval_hours"}
    if scheduler_keys & set(updates.keys()):
        from services.scheduler import refresh_scheduler_intervals
        refresh_scheduler_intervals()

    return {"status": "ok"}
