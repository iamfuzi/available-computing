import json

from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func
from datetime import datetime, timedelta, timezone

from database import get_session
from models import CandidateProvider, HealthRecord, Notification, Channel, Model, RequestLog
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
    # "待确认" = distinct models referenced by open policy_change alerts —
    # the actionable manual-review queue. Reconcile has already closed zombie
    # alerts (settled/removed models), and legacy per-run duplicates for one
    # model must not inflate the number. Plain is_free IS NULL would also
    # count ~70 whitelist-delisted catalog models nobody needs to review.
    policy_alerts = session.exec(
        select(Notification)
        .where(Notification.category == "policy_change")
        .where(Notification.resolved_at == None)  # noqa: E711
        .where(Notification.status != "dismissed")
    ).all()
    pending_policy_change_count = len({
        json.loads(r.payload_json or "{}").get("model_id")
        for r in policy_alerts
    } - {None})
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


@router.get("/request-logs")
def list_request_logs(
    limit: int = 100,
    outcome: str | None = None,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    """Recent terminal proxy requests (7-day retention).

    Filters: ?outcome=fail / success / rejected_local. Post-mortem companion
    to the dashboard — answers "what exactly failed and on which provider"
    after docker logs have rotated away.
    """
    stmt = select(RequestLog).order_by(RequestLog.ts.desc()).limit(min(max(limit, 1), 500))
    if outcome:
        stmt = stmt.where(RequestLog.outcome == outcome)
    rows = session.exec(stmt).all()
    return [
        {
            "ts": r.ts.isoformat(), "request_id": r.request_id,
            "api_key_id": r.api_key_id, "category": r.category,
            "requested_model": r.requested_model, "selected_model": r.selected_model,
            "provider": r.provider, "outcome": r.outcome,
            "status_code": r.status_code, "error_code": r.error_code,
            "latency_ms": r.latency_ms, "attempted": r.attempted,
        }
        for r in rows
    ]
