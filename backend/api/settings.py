from fastapi import APIRouter, Depends
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
        row = session.get(Setting, key)
        if row:
            row.value = str(val)
        else:
            row = Setting(key=key, value=str(val))
        session.add(row)
    session.commit()
    return {"status": "ok"}
