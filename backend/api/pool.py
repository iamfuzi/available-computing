from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func
from datetime import datetime, timezone

from database import get_session
from models import Channel, Model
from api.auth import verify_token

router = APIRouter()


@router.get("/summary")
def pool_summary(session: Session = Depends(get_session), _=Depends(verify_token)):
    total_channels = session.exec(select(func.count(Channel.id))).one()
    enabled_channels = session.exec(
        select(func.count(Channel.id)).where(Channel.enabled == True)
    ).one()

    free_models = session.exec(
        select(Model)
        .where(Model.is_free == True)
        .where(Model.is_active == True)
    ).all()

    health_dist = {"healthy": 0, "slow": 0, "down": 0, "unknown": 0, "rate_limited": 0}
    now = datetime.now(timezone.utc)
    for m in free_models:
        status = m.health_status
        if m.rate_limited_until:
            until = m.rate_limited_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                status = "rate_limited"
        health_dist[status] = health_dist.get(status, 0) + 1

    usable = health_dist.get("healthy", 0)

    return {
        "total_channels": total_channels,
        "enabled_channels": enabled_channels,
        "free_model_count": len(free_models),
        "available_model_count": usable,
        "health_distribution": health_dist,
    }
