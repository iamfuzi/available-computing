import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from adapters import get_adapter, list_providers
from models import Channel


def test_custom_provider_metadata_is_exposed():
    providers = {provider["id"]: provider for provider in list_providers()}
    for provider_id in ("groq", "siliconflow", "openrouter", "zhipu", "agnes"):
        provider = providers[provider_id]
        assert provider["config_type"] == "custom"
        assert provider["setup"]["console_url"].startswith("https://")
        assert provider["compliance"]["note"]
        assert provider["compliance"]["sources"]


@pytest.mark.asyncio
async def test_new_channel_snapshots_provenance_and_compliance(
    app_client, auth_headers, db_session
):
    class StubAdapter:
        provider_id = "groq"
        display_name = "Groq"
        default_base_url = "https://api.groq.com/openai/v1"

        validate_key = AsyncMock(return_value=None)

    with patch("api.channels.get_adapter", return_value=StubAdapter()), patch(
        "api.channels.discover_channel", new=AsyncMock()
    ):
        response = await app_client.post(
            "/api/v1/channels",
            headers=auth_headers,
            json={"provider_type": "groq", "api_key": "gsk_test"},
        )

    assert response.status_code == 201
    channel = db_session.exec(select(Channel).where(Channel.provider_type == "groq")).one()
    assert channel.config_type == "custom_adapter"
    assert channel.discovery_source == "manual"
    compliance = json.loads(channel.compliance_note)
    assert compliance["risk"] == "medium"
    assert compliance["reviewed_at"] == "2026-07-21"
    assert compliance["sources"]


@pytest.mark.asyncio
async def test_blank_key_is_rejected_for_authenticated_provider(app_client, auth_headers):
    response = await app_client.post(
        "/api/v1/channels",
        headers=auth_headers,
        json={"provider_type": "mistral", "api_key": ""},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "API key is required for this provider"


@pytest.mark.asyncio
async def test_anonymous_provider_can_be_added_without_key(
    app_client, auth_headers, db_session
):
    adapter = get_adapter("kilo-code")
    with patch("api.channels.get_adapter", return_value=adapter), patch.object(
        adapter, "validate_key", new=AsyncMock(return_value=None)
    ), patch("api.channels.discover_channel", new=AsyncMock()):
        response = await app_client.post(
            "/api/v1/channels",
            headers=auth_headers,
            json={"provider_type": "kilo-code"},
        )

    assert response.status_code == 201
    assert response.json()["api_key_hint"] == "无需 Key"
    channel = db_session.exec(
        select(Channel).where(Channel.provider_type == "kilo-code")
    ).one()
    assert channel.config_type == "declarative"
