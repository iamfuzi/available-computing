from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from models import CandidateProvider, CandidateSourceState, HealthRecord, Notification
from services.notification_delivery import NotificationDispatcher
from services.notifications import reconcile_notifications, sync_channel_notification, upsert_notification


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


def _policy_alert(session, key: str, model_id: str) -> Notification:
    row = upsert_notification(
        session,
        dedupe_key=key,
        category="policy_change",
        severity="warning",
        title="免费策略疑似变化",
        message="permanent → billing_suspect，请人工确认。",
        action_path=f"/models/{model_id}",
        payload={"model_id": model_id},
    )
    session.commit()
    return row


def _by_key(session, key: str) -> Notification:
    return session.exec(select(Notification).where(Notification.dedupe_key == key)).first()


def test_reconcile_resolves_policy_change_for_settled_models(db_session, sample_channel, sample_model):
    """Alerts for models that already left the suspect state (adjudicated
    paid, restored free, or deleted) are zombies — reconcile must close them,
    otherwise the pool overview keeps asking to confirm settled models."""
    from models import Model
    # Settled: adjudicated paid
    paid = Model(id="mdl-paid", channel_id=sample_channel.id, model_id="m-paid",
                 is_free=False, free_source="manual", is_active=True)
    # Settled: restored free
    free = Model(id="mdl-free", channel_id=sample_channel.id, model_id="m-free",
                 is_free=True, free_source="whitelist", is_active=True)
    # Genuinely still pending
    pending = Model(id="mdl-pending", channel_id=sample_channel.id, model_id="m-pending",
                    is_free=None, free_type="billing_suspect", free_source="event_recheck", is_active=True)
    db_session.add_all([paid, free, pending])
    db_session.commit()

    _policy_alert(db_session, "policy_change:mdl-paid", "mdl-paid")
    _policy_alert(db_session, "policy_change:mdl-free", "mdl-free")
    _policy_alert(db_session, "policy_change:mdl-pending", "mdl-pending")
    # Legacy per-run key for the pending model must survive too (it is still
    # true that this model needs review).
    _policy_alert(db_session, "policy_change:mdl-pending:run-legacy", "mdl-pending")
    # Alert whose model no longer exists at all
    _policy_alert(db_session, "policy_change:mdl-gone", "mdl-gone")

    reconcile_notifications(db_session)

    assert _by_key(db_session, "policy_change:mdl-paid").resolved_at is not None
    assert _by_key(db_session, "policy_change:mdl-free").resolved_at is not None
    assert _by_key(db_session, "policy_change:mdl-gone").resolved_at is not None
    assert _by_key(db_session, "policy_change:mdl-pending").resolved_at is None
    assert _by_key(db_session, "policy_change:mdl-pending:run-legacy").resolved_at is None


@pytest.mark.asyncio
async def test_pool_summary_pending_counts_models_not_alerts(app_client, auth_headers, db_session, sample_channel, sample_model):
    """'待确认' mirrors distinct models awaiting a billing decision; stacking
    several alerts on one model must not inflate it."""
    from models import Model
    pending = Model(id="mdl-p2", channel_id=sample_channel.id, model_id="m-p2",
                    is_free=None, free_type="billing_suspect", is_active=True)
    db_session.add(pending)
    db_session.commit()

    _policy_alert(db_session, "policy_change:mdl-p2", "mdl-p2")
    _policy_alert(db_session, "policy_change:mdl-p2:run-b", "mdl-p2")
    _policy_alert(db_session, "policy_change:mdl-p2:run-c", "mdl-p2")
    # sample_model is is_free=True and settled: an alert for it would be a
    # zombie and must not count either.
    _policy_alert(db_session, "policy_change:mdl-001", sample_model.id)

    response = await app_client.get("/api/v1/pool/summary", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["pending_policy_change_count"] == 1
