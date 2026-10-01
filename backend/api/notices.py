from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from database import get_session
from api.auth import verify_token
from services import notices_center

router = APIRouter()


class NoticeCreate(BaseModel):
    title: str
    body: str
    level: str = "info"  # info / warning / breaking
    action_required: bool = False
    expires_at: str | None = None  # ISO 时间，到期自动隐藏


@router.get("")
def list_notices(_=Depends(verify_token)):
    """管理端完整列表（含已过期，便于审计）。"""
    from services.notices_center import active_notices
    return {"notices": active_notices(use_cache=False)}


@router.post("", status_code=201)
def create_notice_api(
    body: NoticeCreate,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    try:
        return notices_center.create_notice(
            title=body.title, body=body.body, level=body.level,
            action_required=body.action_required, expires_at=body.expires_at,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/{notice_id}", status_code=204)
def delete_notice_api(notice_id: str, _=Depends(verify_token)):
    if not notices_center.delete_notice(notice_id):
        raise HTTPException(404)
