import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import ApiKey, Model


def _add_key(db_session, raw: str, **policy) -> ApiKey:
    key = ApiKey(
        name="policy-test",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:8],
        key_encrypted="",
        is_active=True,
        **policy,
    )
    db_session.add(key)
    db_session.commit()
    return key


@pytest.mark.asyncio
async def test_api_key_create_and_list_round_trip_policy(app_client, auth_headers):
    created = await app_client.post(
        "/api/v1/apikeys",
        headers=auth_headers,
        json={
            "name": "media-tool",
            "provider_whitelist": ["siliconflow"],
            "provider_blacklist": ["openrouter"],
            "rate_limit": {"rpm": 12, "rpd": 345},
            "default_routing_policy": {"prefer": "capability", "min_context": 32000},
        },
    )
    assert created.status_code == 201
    assert created.json()["rate_limit"] == {"rpm": 12, "rpd": 345}
    assert created.json()["default_routing_policy"] == {
        "prefer": "capability",
        "min_context": 32000,
    }

    listed = await app_client.get("/api/v1/apikeys", headers=auth_headers)
    assert listed.status_code == 200
    row = listed.json()[0]
    assert row["provider_whitelist"] == ["siliconflow"]
    assert row["provider_blacklist"] == ["openrouter"]


@pytest.mark.asyncio
async def test_api_key_rejects_overlapping_provider_policy(app_client, auth_headers):
    response = await app_client.post(
        "/api/v1/apikeys",
        headers=auth_headers,
        json={
            "name": "invalid",
            "provider_whitelist": ["groq"],
            "provider_blacklist": ["groq"],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_key_blacklist_hides_and_blocks_provider(
    app_client, db_session, sample_model, sample_channel
):
    raw = "ac_policy_blacklist"
    _add_key(db_session, raw, provider_blacklist='["openrouter"]')
    headers = {"Authorization": f"Bearer {raw}"}

    models = await app_client.get("/v1/models", headers=headers)
    assert models.status_code == 200
    assert models.json()["data"] == []

    chat = await app_client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": sample_model.model_id, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code == 404
    assert chat.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_request_policy_can_only_raise_key_minimum_context(
    app_client, db_session, sample_model, sample_channel
):
    raw = "ac_policy_context"
    _add_key(db_session, raw, default_min_context=32000)
    sample_model.context_length = 16000
    db_session.add(sample_model)
    db_session.commit()

    response = await app_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "model": "auto:text",
            "messages": [{"role": "user", "content": "hi"}],
            "routing_policy": {"min_context": 8000},
        },
    )
    assert response.status_code == 404
    assert "effective routing policy" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_request_exclude_filters_admin_route(
    app_client, auth_headers, sample_model, sample_channel
):
    response = await app_client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "auto:text",
            "messages": [{"role": "user", "content": "hi"}],
            "routing_policy": {"exclude": ["openrouter"]},
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fallback_chain_and_trace_headers(
    app_client, auth_headers, db_session, sample_model, sample_channel
):
    sample_model.last_verified_at = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    db_session.add(sample_model)
    db_session.commit()

    with patch("httpx.AsyncClient") as MockClient:
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]
        }
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=upstream)
        MockClient.return_value = client

        response = await app_client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "missing-primary",
                "messages": [{"role": "user", "content": "hi"}],
                "routing_policy": {"fallback_chain": [sample_model.model_id]},
            },
        )

    assert response.status_code == 200
    assert response.headers["X-AC-Actual-Model"] == f"openrouter/{sample_model.model_id}"
    assert response.headers["X-AC-Fallback-Triggered"] == "true"
    assert response.headers["X-AC-Model-Verified-At"].startswith("2026-07-20T08:00:00")


@pytest.mark.asyncio
async def test_key_specific_rpm_is_aggregate_across_routes(app_client, db_session):
    raw = "ac_policy_rpm"
    _add_key(db_session, raw, rate_limit_rpm=1)
    headers = {"Authorization": f"Bearer {raw}"}

    first = await app_client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert first.status_code == 404

    second = await app_client.post(
        "/v1/embeddings",
        headers=headers,
        json={"model": "missing", "input": "hi"},
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "local_rate_limited"
