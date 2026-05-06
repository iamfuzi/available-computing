from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func

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

    health_dist = {"healthy": 0, "slow": 0, "down": 0, "unknown": 0}
    for m in free_models:
        health_dist[m.health_status] = health_dist.get(m.health_status, 0) + 1

    return {
        "total_channels": total_channels,
        "enabled_channels": enabled_channels,
        "free_model_count": len(free_models),
        "health_distribution": health_dist,
    }
