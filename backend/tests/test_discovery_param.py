"""Tests that discovery populates Model.param_size correctly.

The param_size on a stored model is the merge of two signals: the id parser
(for open-source ids like ``qwen2.5-72b``) and the whitelist override (for
closed-source ids like ``glm-4-flash``). These tests mock the adapter so no
network is needed, and assert what lands in the DB.
"""
import base64
import pytest
from unittest.mock import AsyncMock, patch
from sqlmodel import Session, select
from adapters.base import ModelInfo


async def _run_discovery(raw_models, provider_type, channel, db_session, key="sk-test"):
    """Invoke discover_channel with a stubbed adapter returning raw_models.

    ``channel`` must already be persisted with the given ``provider_type`` so
    that whitelist matching inside discovery uses the right provider key.
    """
    from services import discovery

    class _StubAdapter:
        default_base_url = "https://stub.example/v1"
        provider_id = provider_type

        async def list_models(self, *a, **kw):
            return raw_models

        async def fetch_free_model_ids(self, *a, **kw):
            return None  # let _determine_free fall through to detect/whitelist

        def detect_free_from_api(self, m):
            return None  # defer to whitelist

        async def health_check(self, *a, **kw):
            from adapters.base import HealthInfo
            return HealthInfo(status="healthy", response_ms=100)

    # Skip the health-probe sweep at the tail of discover_channel.
    with patch("services.discovery.get_adapter", return_value=_StubAdapter()), \
         patch("services.health.probe_channel_models", new=AsyncMock()), \
         patch("services.discovery.events.broadcast", new=AsyncMock()):
        await discovery.discover_channel(channel.id, key)

    db_session.expire_all()
    return db_session


def _channel_with(db_session, fixed_salt, provider_type, ch_id):
    from models import Channel, Setting
    from services.crypto import encrypt
    db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
    ch = Channel(
        id=ch_id, provider_type=provider_type, name=f"Test {provider_type}",
        api_key_enc=encrypt("sk-test-api-key", "test-admin-password", fixed_salt),
        enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    return ch


@pytest.mark.asyncio
async def test_param_size_parsed_from_open_source_id(db_session, fixed_salt):
    # 'qwen2.5-72b' carries a parseable size; the whitelist need not list it.
    from models import Model
    ch = _channel_with(db_session, fixed_salt, "siliconflow", "ch-sf")
    raw = [ModelInfo(model_id="Qwen/Qwen2.5-72B-Instruct", display_name="Qwen 72B", category="text")]
    await _run_discovery(raw, "siliconflow", ch, db_session)

    m = db_session.exec(select(Model).where(Model.model_id == "Qwen/Qwen2.5-72B-Instruct")).first()
    assert m is not None
    assert m.param_size == 72.0


@pytest.mark.asyncio
async def test_param_size_falls_back_to_whitelist(db_session, fixed_salt):
    # 'glm-4-flash' has no parseable marker, so the whitelist's param_size
    # (130, set in providers.yaml) must be used.
    from models import Model
    ch = _channel_with(db_session, fixed_salt, "zhipu", "ch-zhipu")
    raw = [ModelInfo(model_id="glm-4-flash", display_name="GLM-4 Flash", category="text")]
    await _run_discovery(raw, "zhipu", ch, db_session)

    m = db_session.exec(select(Model).where(Model.model_id == "glm-4-flash")).first()
    assert m is not None
    assert m.param_size == 130


@pytest.mark.asyncio
async def test_param_size_none_when_unparseable_and_not_in_whitelist(db_session, fixed_salt):
    from models import Model
    ch = _channel_with(db_session, fixed_salt, "openrouter", "ch-or")
    raw = [ModelInfo(model_id="gpt-4o", display_name="GPT-4o", category="text")]
    await _run_discovery(raw, "openrouter", ch, db_session)

    m = db_session.exec(select(Model).where(Model.model_id == "gpt-4o")).first()
    assert m is not None
    assert m.param_size is None


@pytest.mark.asyncio
async def test_param_size_moe_uses_activated(db_session, fixed_salt):
    from models import Model
    ch = _channel_with(db_session, fixed_salt, "openrouter", "ch-or2")
    raw = [ModelInfo(model_id="nvidia/nemotron-3-ultra-550b-a55b", display_name="Nemotron", category="text")]
    await _run_discovery(raw, "openrouter", ch, db_session)

    m = db_session.exec(select(Model).where(Model.model_id == "nvidia/nemotron-3-ultra-550b-a55b")).first()
    assert m is not None
    assert m.param_size == 55.0
