"""Regression tests: SiliconFlow paid families must never be admitted as free.

Billing evidence (2026-09-29): "LoRA/Qwen/..." community fine-tunes plus
"Qwen/Qwen2.5-14B-Instruct" / "Qwen/Qwen2.5-72B-Instruct" were charged on the
SiliconFlow bill while AC still routed and health-probed them as free models.
Two gates keep them out, pinned here:

1. adapter prefix rule — "Pro/" and "LoRA/" return a definitive paid verdict
   that the whitelist cannot override (discovery Step 2 short-circuits);
2. whitelist curation — the 14B/72B entries were removed from
   whitelist/providers.yaml, so discovery marks them unknown (not probed,
   not routed) instead of free.
"""
import base64
import pytest
from unittest.mock import AsyncMock, patch
from sqlmodel import select

from adapters.base import ModelInfo
from adapters.siliconflow import SiliconFlowAdapter


def _info(model_id: str) -> ModelInfo:
    return ModelInfo(model_id=model_id, display_name=model_id, category="text")


class TestDetectFreePrefixRules:
    def test_lora_prefix_is_paid(self):
        # Community fine-tunes inherit the base model id as a suffix — the
        # reason the whitelist alone cannot be trusted for them.
        result = SiliconFlowAdapter().detect_free_from_api(_info("LoRA/Qwen/Qwen2.5-7B-Instruct"))
        assert result == {"is_free": False, "free_type": "permanent", "free_source": "prefix_rule"}

    def test_pro_prefix_is_paid(self):
        result = SiliconFlowAdapter().detect_free_from_api(_info("Pro/Qwen/Qwen2.5-7B-Instruct"))
        assert result == {"is_free": False, "free_type": "permanent", "free_source": "prefix_rule"}

    def test_plain_id_defers_to_whitelist(self):
        # No prefix verdict → discovery falls through to the whitelist step.
        assert SiliconFlowAdapter().detect_free_from_api(_info("Qwen/Qwen2.5-7B-Instruct")) is None


async def _run_discovery(raw_models, channel, db_session, key="sk-test"):
    """discover_channel against a stub adapter whose free detection delegates
    to the REAL SiliconFlowAdapter, so the production gate order is exercised."""
    from services import discovery

    real_adapter = SiliconFlowAdapter()

    class _StubAdapter:
        default_base_url = real_adapter.default_base_url
        provider_id = "siliconflow"

        async def list_models(self, *a, **kw):
            return raw_models

        async def fetch_free_model_ids(self, *a, **kw):
            return None

        def detect_free_from_api(self, m):
            return real_adapter.detect_free_from_api(m)

        async def health_check(self, *a, **kw):
            from adapters.base import HealthInfo
            return HealthInfo(status="healthy", response_ms=100)

    with patch("services.discovery.get_adapter", return_value=_StubAdapter()), \
         patch("services.health.probe_channel_models", new=AsyncMock()), \
         patch("services.discovery.events.broadcast", new=AsyncMock()):
        await discovery.discover_channel(channel.id, key)

    db_session.expire_all()
    return db_session


def _siliconflow_channel(db_session, fixed_salt):
    from models import Channel, Setting
    from services.crypto import encrypt
    db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
    ch = Channel(
        id="ch-sf-gate", provider_type="siliconflow", name="Test SiliconFlow",
        api_key_enc=encrypt("sk-test-api-key", "test-admin-password", fixed_salt),
        enabled=True,
    )
    db_session.add(ch)
    db_session.commit()
    return ch


@pytest.mark.asyncio
async def test_paid_families_stay_out_of_free_pool(db_session, fixed_salt):
    from models import Model
    ch = _siliconflow_channel(db_session, fixed_salt)
    raw = [
        _info("LoRA/Qwen/Qwen2.5-7B-Instruct"),
        _info("Qwen/Qwen2.5-14B-Instruct"),
        _info("Qwen/Qwen2.5-72B-Instruct"),
        _info("Qwen/Qwen2.5-7B-Instruct"),
    ]
    await _run_discovery(raw, ch, db_session)

    rows = {m.model_id: m for m in db_session.exec(select(Model).where(Model.channel_id == ch.id)).all()}

    # LoRA fine-tunes: definitive paid verdict, never probed or routed.
    assert rows["LoRA/Qwen/Qwen2.5-7B-Instruct"].is_free is False
    assert rows["LoRA/Qwen/Qwen2.5-7B-Instruct"].free_source == "prefix_rule"

    # 14B/72B: delisted from the whitelist → unknown, not free.
    for mid in ("Qwen/Qwen2.5-14B-Instruct", "Qwen/Qwen2.5-72B-Instruct"):
        assert rows[mid].is_free is None
        assert rows[mid].free_source == "unknown"

    # The still-free 7B base model keeps passing through the whitelist.
    assert rows["Qwen/Qwen2.5-7B-Instruct"].is_free is True
    assert rows["Qwen/Qwen2.5-7B-Instruct"].free_source == "whitelist"


@pytest.mark.asyncio
async def test_existing_paid_rows_are_flipped_back_on_rediscovery(db_session, fixed_salt):
    """Rows admitted by the old buggy logic must be corrected by the next
    discovery run, without needing manual DB surgery."""
    from models import Model
    ch = _siliconflow_channel(db_session, fixed_salt)

    # Simulate the pre-fix state: charged models sitting in the pool as free.
    for mid, source in (
        ("LoRA/Qwen/Qwen2.5-7B-Instruct", "whitelist"),
        ("Qwen/Qwen2.5-72B-Instruct", "whitelist"),
    ):
        db_session.add(Model(
            channel_id=ch.id, model_id=mid, display_name=mid, category="text",
            is_free=True, free_type="permanent", free_source=source,
            health_status="healthy", is_active=True,
        ))
    db_session.commit()

    await _run_discovery([
        _info("LoRA/Qwen/Qwen2.5-7B-Instruct"),
        _info("Qwen/Qwen2.5-72B-Instruct"),
    ], ch, db_session)

    rows = {m.model_id: m for m in db_session.exec(select(Model).where(Model.channel_id == ch.id)).all()}
    assert rows["LoRA/Qwen/Qwen2.5-7B-Instruct"].is_free is False
    assert rows["Qwen/Qwen2.5-72B-Instruct"].is_free is None
