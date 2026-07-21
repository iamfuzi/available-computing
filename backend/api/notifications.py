import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import verify_token
from database import get_session
from models import Notification
from services.notifications import reconcile_notifications


router = APIRouter()


class NotificationUpdate(BaseModel):
    status: Literal["read", "dismissed"]


def _as_dict(row: Notification) -> dict:
    return {
        **row.model_dump(exclude={"payload_json"}),
        "payload": json.loads(row.payload_json or "{}"),
    }


@router.get("")
def list_notifications(
    include_resolved: bool = False,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    reconcile_notifications(session)
    statement = select(Notification).where(Notification.status != "dismissed")
    if not include_resolved:
        statement = statement.where(Notification.resolved_at == None)
    rows = session.exec(statement.order_by(Notification.updated_at.desc()).limit(100)).all()
    return [_as_dict(row) for row in rows]


@router.get("/unread-count")
def unread_count(
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    reconcile_notifications(session)
    rows = session.exec(
        select(Notification)
        .where(Notification.status == "unread")
        .where(Notification.resolved_at == None)
    ).all()
    return {"count": len(rows)}


@router.post("/read-all")
async def read_all(
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    now = datetime.now(timezone.utc)
    rows = session.exec(
        select(Notification)
        .where(Notification.status == "unread")
        .where(Notification.resolved_at == None)
    ).all()
    for row in rows:
        row.status = "read"
        row.read_at = now
        row.updated_at = now
        session.add(row)
    session.commit()
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()
    return {"updated": len(rows)}


@router.patch("/{notification_id}")
async def update_notification(
    notification_id: str,
    body: NotificationUpdate,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    row = session.get(Notification, notification_id)
    if not row:
        raise HTTPException(404, "Notification not found")
    now = datetime.now(timezone.utc)
    row.status = body.status
    row.read_at = now
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()
    return _as_dict(row)
