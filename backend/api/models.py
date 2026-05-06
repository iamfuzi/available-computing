from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlmodel import Session, select

from database import get_session
from models import Model, HealthRecord, Channel
from api.auth import verify_token

router = APIRouter()


@router.get("")
def list_models(
    provider: Optional[str] = None,
    category: Optional[str] = None,
    free_only: bool = True,
    healthy_only: bool = False,
    q: Optional[str] = None,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    stmt = select(Model).where(Model.is_active == True)

    if free_only:
        stmt = stmt.where(Model.is_free == True)
    if healthy_only:
        stmt = stmt.where(Model.health_status == "healthy")
    if category:
        stmt = stmt.where(Model.category == category)
    if q:
        stmt = stmt.where(Model.model_id.contains(q) | Model.display_name.contains(q))

    models = session.exec(stmt).all()

    if provider:
        channels = {
            ch.id: ch
            for ch in session.exec(select(Channel).where(Channel.provider_type == provider)).all()
        }
        models = [m for m in models if m.channel_id in channels]

    # Enrich with provider info and sort by response time
    channel_map = {
        ch.id: ch for ch in session.exec(select(Channel)).all()
    }

    result = []
    for m in models:
        ch = channel_map.get(m.channel_id)
        result.append({
            **m.model_dump(),
            "provider_type": ch.provider_type if ch else None,
            "provider_name": ch.name if ch else None,
            "base_url": ch.base_url if ch else None,
        })

    # Sort: known response time first (ascending), then unknown
    result.sort(key=lambda x: (x["last_response_ms"] is None, x["last_response_ms"] or 0))
    return result


@router.get("/{model_id}")
def get_model(
    model_id: str,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    m = session.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    ch = session.get(Channel, m.channel_id)
    return {
        **m.model_dump(),
        "provider_type": ch.provider_type if ch else None,
        "provider_name": ch.name if ch else None,
        "base_url": ch.base_url if ch else None,
    }


@router.get("/{model_id}/health-history")
def get_health_history(
    model_id: str,
    period: str = "24h",
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    m = session.get(Model, model_id)
    if not m:
        raise HTTPException(404)

    hours = 168 if period == "7d" else 24
    since = datetime.utcnow() - timedelta(hours=hours)

    records = session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id == model_id)
        .where(HealthRecord.checked_at >= since)
        .order_by(HealthRecord.checked_at)
    ).all()

    return [r.model_dump() for r in records]
