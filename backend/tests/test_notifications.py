from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from models import CandidateProvider, CandidateSourceState, HealthRecord, Notification
from services.notification_delivery import NotificationDispatcher
from services.notifications import reconcile_notifications, sync_channel_notification


def test_channel_alert_is_deduplicated_and_resolved(db_session, sample_channel):
    sample_channel.status = "key_invalid"
    sample_channel.status_reason = "upstream_401"
    sync_channel_notification(db_session, sample_channel)
    sync_channel_notification(db_session, sample_channel)
    db_session.commit()

    rows = db_session.exec(select(Notification)).all()
    assert len(rows) == 1
    assert rows[0].severity == "critical"
    assert rows[0].resolved_at is None

    sample_channel.status = "active"
    sync_channel_notification(db_session, sample_channel)
    db_session.commit()
    assert db_session.get(Notification, rows[0].id).resolved_at is not None


def test_reconcile_only_counts_admissible_pending_candidates(db_session):
    db_session.add(CandidateProvider(
        provider_id="eligible",
        name="Eligible",
        homepage_url="https://eligible.example",
        admission_status="review_required",
        status="pending",
    ))
    db_session.add(CandidateProvider(
        provider_id="trial",
        name="Trial",
        homepage_url="https://trial.example",
        admission_status="excluded",
        status="pending",
    ))
    reconcile_notifications(db_session)

    row = db_session.exec(
        select(Notification).where(Notification.dedupe_key == "candidate:pending")
    ).one()
    assert row.title == "发现 1 个待审核免费厂商"


def test_reconcile_creates_candidate_source_failure_alert(db_session):
    db_session.add(CandidateSourceState(
        source_id="broken",
        url="https://source.example",
        consecutive_failures=2,
        last_error="parse failed",
        needs_attention=True,
    ))
    db_session.commit()
    reconcile_notifications(db_session)
    row = db_session.exec(
        select(Notification).where(Notification.dedupe_key == "candidate_source:broken")
    ).one()
    assert row.category == "candidate_source"
    assert "parse failed" in row.message


@pytest.mark.asyncio
async def test_notification_api_read_and_dismiss(app_client, auth_headers, db_session, sample_channel):
    sample_channel.status = "key_invalid"
    db_session.add(sample_channel)
    db_session.commit()

    response = await app_client.get("/api/v1/notifications", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()[0]
    assert item["status"] == "unread"

    response = await app_client.patch(
        f"/api/v1/notifications/{item['id']}",
        headers=auth_headers,
        json={"status": "read"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "read"

    response = await app_client.patch(
        f"/api/v1/notifications/{item['id']}",
        headers=auth_headers,
        json={"status": "dismissed"},
    )
    assert response.status_code == 200
    response = await app_client.get("/api/v1/notifications", headers=auth_headers)
    assert response.json() == []


@pytest.mark.asyncio
async def test_pool_summary_counts_distinct_24h_rechecks(
    app_client, auth_headers, db_session, sample_model
):
    now = datetime.now(timezone.utc)
    for run_id in ("run-a", "run-a", "run-b"):
        db_session.add(HealthRecord(
            model_id=sample_model.id,
            checked_at=now - timedelta(hours=1),
            status="down",
            error_code="rate_limited",
            verification_method="active_event_triggered",
            check_run_id=run_id,
        ))
    db_session.commit()
    response = await app_client.get("/api/v1/pool/summary", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["recheck_count_24h"] == 2


@pytest.mark.asyncio
async def test_notification_dispatcher_accepts_webhook_style_sink():
    received = []

    class Sink:
        async def send(self, payload: dict) -> None:
            received.append(payload)

    dispatcher = NotificationDispatcher()
    dispatcher.register(Sink())
    await dispatcher.dispatch({"title": "key invalid"})
    assert received == [{"title": "key invalid"}]
