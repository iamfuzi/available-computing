"""Notification persistence, deduplication, and state reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from models import CandidateProvider, CandidateSourceState, Channel, Notification


CHANNEL_ALERT_STATUSES = {"key_invalid", "key_expired", "suspended"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upsert_notification(
    session: Session,
    *,
    dedupe_key: str,
    category: str,
    severity: str,
    title: str,
    message: str,
    action_path: str | None = None,
    payload: dict | None = None,
) -> Notification:
    """Create once per condition and reopen only after it had resolved."""
    now = _now()
    row = session.exec(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    ).first()
    if row is None:
        row = Notification(
            dedupe_key=dedupe_key,
            category=category,
            severity=severity,
            title=title,
            message=message,
            action_path=action_path,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
    else:
        recurrence = row.resolved_at is not None
        row.category = category
        row.severity = severity
        row.title = title
        row.message = message
        row.action_path = action_path
        row.payload_json = json.dumps(payload or {}, ensure_ascii=False)
        row.updated_at = now
        row.resolved_at = None
        if recurrence:
            row.status = "unread"
            row.read_at = None
            row.created_at = now
    session.add(row)
    return row


def resolve_notification(session: Session, dedupe_key: str) -> None:
    row = session.exec(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    ).first()
    if row and row.resolved_at is None:
        row.resolved_at = _now()
        row.updated_at = row.resolved_at
        session.add(row)


def sync_channel_notification(session: Session, channel: Channel) -> None:
    key = f"channel:{channel.id}"
    if channel.status not in CHANNEL_ALERT_STATUSES:
        resolve_notification(session, key)
        return
    labels = {
        "key_invalid": ("厂商 Key 已失效", "检测到 401/403，请更新 API Key。", "critical"),
        "key_expired": ("厂商 Key 已过期", "该 API Key 已超过已知有效期。", "critical"),
        "suspended": ("厂商渠道疑似受限", "连续调用失败，渠道已暂停参与路由。", "warning"),
    }
    title, message, severity = labels[channel.status]
    upsert_notification(
        session,
        dedupe_key=key,
        category="channel",
        severity=severity,
        title=f"{channel.name}：{title}",
        message=channel.status_reason or message,
        action_path="/channels",
        payload={"channel_id": channel.id, "status": channel.status},
    )


def reconcile_notifications(session: Session) -> None:
    """Repair derived notifications from current state without duplicates."""
    now = _now()
    channels = session.exec(select(Channel)).all()
    for channel in channels:
        expires_at = channel.key_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at <= now and channel.status != "key_expired":
            channel.status = "key_expired"
            channel.status_reason = "known_expiration_reached"
            channel.status_changed_at = now
            session.add(channel)
        sync_channel_notification(session, channel)

    pending_candidates = session.exec(
        select(CandidateProvider)
        .where(CandidateProvider.is_present == True)
        .where(CandidateProvider.status == "pending")
        .where(CandidateProvider.admission_status == "review_required")
    ).all()
    if pending_candidates:
        upsert_notification(
            session,
            dedupe_key="candidate:pending",
            category="candidate",
            severity="info",
            title=f"发现 {len(pending_candidates)} 个待审核免费厂商",
            message="仅展示通过信用卡、试用和 credits 初筛的候选，仍需核验官网条款。",
            action_path="/candidates",
            payload={"count": len(pending_candidates)},
        )
    else:
        resolve_notification(session, "candidate:pending")

    source_rows = session.exec(select(CandidateSourceState)).all()
    active_source_keys: set[str] = set()
    for source in source_rows:
        key = f"candidate_source:{source.source_id}"
        if source.needs_attention:
            active_source_keys.add(key)
            upsert_notification(
                session,
                dedupe_key=key,
                category="candidate_source",
                severity="warning",
                title=f"候选源 {source.source_id} 连续抓取失败",
                message=source.last_error or "候选数据源需要检查。",
                action_path="/candidates",
                payload={
                    "source_id": source.source_id,
                    "consecutive_failures": source.consecutive_failures,
                },
            )
        else:
            resolve_notification(session, key)

    session.commit()


async def broadcast_notifications_updated() -> None:
    """Push a lightweight invalidation event; clients then reload canonical state."""
    from services import events
    await events.broadcast("notifications_updated", {})
