from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import Model, HealthRecord, Channel, Notification
from api.auth import verify_token

router = APIRouter()


class ModelReviewRequest(BaseModel):
    decision: Literal["paid", "free"]
    # Only meaningful for decision="free"; defaults to permanent.
    free_type: Optional[Literal["permanent", "quota", "grant"]] = None


def _model_with_provider(session: Session, m: Model) -> dict:
    ch = session.get(Channel, m.channel_id)
    return {
        **m.model_dump(),
        "provider_type": ch.provider_type if ch else None,
        "provider_name": ch.name if ch else None,
        "base_url": ch.base_url if ch else None,
    }


@router.get("")
def list_models(
    provider: Optional[str] = None,
    category: Optional[str] = None,
    free_only: bool = True,
    healthy_only: bool = True,
    routable_only: bool = False,
    hide_down: bool = True,
    include_rate_limited: bool = False,
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    stmt = select(Model).where(Model.is_active == True)

    if free_only:
        stmt = stmt.where(Model.is_free == True)
    if routable_only:
        # 与实际路由口径一致（chat_candidates）：slow = 降权但可路由。
        # "健康"只是亚秒分档线，不是可用性线——两个口径曾让管理员误以为
        # 大部分模型不参与服务（2026-09-30）。routable_only 是更宽的口径，
        # 显式覆盖 healthy_only，避免两个开关叠加成意外的交集。
        stmt = stmt.where(Model.health_status.in_(["healthy", "slow"]))
    elif healthy_only:
        stmt = stmt.where(Model.health_status == "healthy")
    if hide_down:
        stmt = stmt.where(Model.health_status != "down").where(Model.health_status != "rate_limited")
    if category:
        stmt = stmt.where(Model.category == category)
    if q:
        stmt = stmt.where(Model.model_id.contains(q) | Model.display_name.contains(q))

    models = session.exec(stmt).all()
    if not include_rate_limited:
        now = datetime.now(timezone.utc)

        def is_cooling_down(model: Model) -> bool:
            if not model.rate_limited_until:
                return False
            until = model.rate_limited_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            return until > now

        models = [m for m in models if not is_cooling_down(m)]

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

    # Sort: default is latency ascending (fastest first); sort_by=smart is
    # param_size descending (largest first, None last) so the UI can mirror
    # the auto:smart / auto:fast router choice.
    if sort_by == "smart":
        result.sort(key=lambda x: (x["param_size"] is None, -(x["param_size"] or 0)))
    else:
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
    return _model_with_provider(session, m)


@router.post("/{model_id}/review")
async def review_model(
    model_id: str,
    body: ModelReviewRequest,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    """Manual adjudication of a model's billing state.

    This closes the loop for billing_suspect flags (event-triggered rechecks
    asking 请人工确认): the admin confirms the model is paid (removed from the
    free pool, routing and probing) or still free (restored). The decision is
    recorded with free_source="manual" and discovery will not overwrite it.
    """
    m = session.get(Model, model_id)
    if not m:
        raise HTTPException(404)

    if body.decision == "paid":
        m.is_free = False
        m.free_type = None
    else:
        m.is_free = True
        m.free_type = body.free_type or "permanent"
    m.free_source = "manual"
    m.consecutive_billing_failures = 0
    session.add(m)

    from services.event_recheck import cancel_pending_rechecks
    cancel_pending_rechecks(model_id)

    # Resolve every open policy_change notification for this model (legacy
    # keys carry a check_run suffix; current keys are one per model); the
    # adjudication supersedes the automatic suspicion.
    from services.notifications import resolve_notification, broadcast_notifications_updated
    open_alerts = session.exec(
        select(Notification)
        .where(Notification.dedupe_key.startswith(f"policy_change:{model_id}"))
        .where(Notification.resolved_at == None)  # noqa: E711
    ).all()
    for row in open_alerts:
        resolve_notification(session, row.dedupe_key)

    session.commit()
    session.refresh(m)
    await broadcast_notifications_updated()
    return _model_with_provider(session, m)


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
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    records = session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id == model_id)
        .where(HealthRecord.checked_at >= since)
        .order_by(HealthRecord.checked_at)
    ).all()

    return [r.model_dump() for r in records]
