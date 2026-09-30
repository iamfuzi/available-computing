"""POST /models/{id}/review — manual billing adjudication.

billing_suspect flags (event recheck finding confirmed 402s) previously
dead-ended: the notification said 请人工确认 but no API could confirm either
way. These tests pin the review loop:

1. paid → is_free=False, out of the free pool, policy_change alerts resolved;
2. free → is_free=True with the chosen free_type;
3. free_source="manual" survives rediscovery (automatic signals must not
   silently overwrite an admin adjudication).
"""
import base64
import pytest
from unittest.mock import AsyncMock, patch
from sqlmodel import select

from adapters.base import ModelInfo
from adapters.siliconflow import SiliconFlowAdapter
from models import Channel, Model, Notification, Setting
from services.crypto import encrypt
from services.notifications import upsert_notification


def _auth_headers():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    token = client.post("/api/v1/auth/login", json={"password": "test-admin-password"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}, client


def _suspect_model(db_session, sample_channel):
    m = Model(
        id="mdl-suspect",
        channel_id=sample_channel.id,
        model_id="LoRA/Qwen/Qwen2.5-72B-Instruct",
        category="text",
        is_free=None,
        free_type="billing_suspect",
        free_source="event_recheck",
        health_status="slow",
        is_active=True,
        consecutive_billing_failures=3,
    )
    db_session.add(m)
    upsert_notification(
        db_session,
        dedupe_key=f"policy_change:{m.id}:run-1",
        category="policy_change",
        severity="warning",
        title="免费策略疑似变化",
        message="permanent → billing_suspect，请人工确认。",
        action_path=f"/models/{m.id}",
    )
    upsert_notification(
        db_session,
        dedupe_key=f"policy_change:{m.id}:run-2",
        category="policy_change",
        severity="warning",
        title="免费策略疑似变化",
        message="再次触发，请人工确认。",
        action_path=f"/models/{m.id}",
    )
    db_session.commit()
    return m


def test_review_paid_resolves_suspect_and_notifications(db_session, sample_channel):
    headers, client = _auth_headers()
    m = _suspect_model(db_session, sample_channel)

    res = client.post("/api/v1/models/mdl-suspect/review", json={"decision": "paid"}, headers=headers)
    assert res.status_code == 200

    db_session.expire_all()
    row = db_session.get(Model, m.id)
    assert row.is_free is False
    assert row.free_type is None
    assert row.free_source == "manual"
    assert row.consecutive_billing_failures == 0

    alerts = db_session.exec(
        select(Notification).where(Notification.dedupe_key.startswith(f"policy_change:{m.id}:"))
    ).all()
    assert alerts and all(a.resolved_at is not None for a in alerts)


def test_review_free_restores_with_chosen_type(db_session, sample_channel):
    headers, client = _auth_headers()
    m = _suspect_model(db_session, sample_channel)

    res = client.post(
        "/api/v1/models/mdl-suspect/review",
        json={"decision": "free", "free_type": "quota"},
        headers=headers,
    )
    assert res.status_code == 200

    db_session.expire_all()
    row = db_session.get(Model, m.id)
    assert row.is_free is True
    assert row.free_type == "quota"
    assert row.free_source == "manual"


def test_review_defaults_to_permanent_free(db_session, sample_channel):
    headers, client = _auth_headers()
    _suspect_model(db_session, sample_channel)

    res = client.post("/api/v1/models/mdl-suspect/review", json={"decision": "free"}, headers=headers)
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(Model, "mdl-suspect").free_type == "permanent"


def test_review_unknown_model_404():
    headers, client = _auth_headers()
    assert client.post(
        "/api/v1/models/nope/review", json={"decision": "paid"}, headers=headers
    ).status_code == 404


def test_review_rejects_invalid_decision(db_session, sample_channel):
    headers, client = _auth_headers()
    _suspect_model(db_session, sample_channel)
    res = client.post(
        "/api/v1/models/mdl-suspect/review", json={"decision": "maybe"}, headers=headers
    )
    assert res.status_code == 422


def _siliconflow_channel(db_session, fixed_salt):
    db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
    ch = Channel(
        id="ch-review", provider_type="siliconflow", name="Test SiliconFlow",
        api_key_enc=encrypt("sk-test-api-key", "test-admin-password", fixed_salt),
        enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    return ch


@pytest.mark.asyncio
async def test_manual_decision_survives_rediscovery(db_session, fixed_salt):
    """An admin 'paid' verdict must not be flipped back to free by the next
    discovery run, even when the whitelist would admit the model."""
    ch = _siliconflow_channel(db_session, fixed_salt)
    # Qwen2.5-7B passes the whitelist as free in the gate tests; seed it as
    # manually adjudicated paid.
    db_session.add(Model(
        id="mdl-manual", channel_id=ch.id, model_id="Qwen/Qwen2.5-7B-Instruct",
        category="text", is_free=False, free_type=None, free_source="manual",
        is_active=True,
    ))
    db_session.commit()

    real_adapter = SiliconFlowAdapter()

    class _StubAdapter:
        default_base_url = real_adapter.default_base_url
        provider_id = "siliconflow"

        async def list_models(self, *a, **kw):
            return [ModelInfo(model_id="Qwen/Qwen2.5-7B-Instruct", display_name="q", category="text")]

        async def fetch_free_model_ids(self, *a, **kw):
            return None

        def detect_free_from_api(self, m):
            return real_adapter.detect_free_from_api(m)

        async def health_check(self, *a, **kw):
            from adapters.base import HealthInfo
            return HealthInfo(status="healthy", response_ms=100)

    from services import discovery
    with patch("services.discovery.get_adapter", return_value=_StubAdapter()), \
         patch("services.health.probe_channel_models", new=AsyncMock()), \
         patch("services.discovery.events.broadcast", new=AsyncMock()):
        await discovery.discover_channel(ch.id)

    db_session.expire_all()
    row = db_session.get(Model, "mdl-manual")
    assert row.is_free is False
    assert row.free_source == "manual"
