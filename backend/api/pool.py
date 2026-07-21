from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func
from datetime import datetime, timedelta, timezone

from database import get_session
from models import CandidateProvider, HealthRecord, Notification, Channel, Model
from api.auth import verify_token
from services.notifications import CHANNEL_ALERT_STATUSES, reconcile_notifications

router = APIRouter()


@router.get("/summary")
def pool_summary(session: Session = Depends(get_session), _=Depends(verify_token)):
    reconcile_notifications(session)
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

    usable = health_dist.get("healthy", 0) + health_dist.get("slow", 0)

    invalid_key_count = len(session.exec(
        select(Channel).where(Channel.status.in_(CHANNEL_ALERT_STATUSES))
    ).all())
    pending_candidate_count = len(session.exec(
        select(CandidateProvider)
        .where(CandidateProvider.is_present == True)
        .where(CandidateProvider.status == "pending")
        .where(CandidateProvider.admission_status == "review_required")
    ).all())
    pending_policy_change_count = len(session.exec(
        select(Notification)
        .where(Notification.category == "policy_change")
        .where(Notification.resolved_at == None)
        .where(Notification.status != "dismissed")
    ).all())
    unread_notification_count = len(session.exec(
        select(Notification)
        .where(Notification.status == "unread")
        .where(Notification.resolved_at == None)
    ).all())
    day_ago = now - timedelta(hours=24)
    rechecks = session.exec(
        select(HealthRecord)
        .where(HealthRecord.verification_method == "active_event_triggered")
        .where(HealthRecord.checked_at >= day_ago)
    ).all()
    recheck_count_24h = len({record.check_run_id or str(record.id) for record in rechecks})

    return {
        "total_channels": total_channels,
        "enabled_channels": enabled_channels,
        "free_model_count": len(free_models),
        "available_model_count": usable,
        "health_distribution": health_dist,
        "invalid_key_count": invalid_key_count,
        "pending_candidate_count": pending_candidate_count,
        "pending_policy_change_count": pending_policy_change_count,
        "recheck_count_24h": recheck_count_24h,
        "unread_notification_count": unread_notification_count,
    }
