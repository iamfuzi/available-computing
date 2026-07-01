import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone, timedelta
from sqlmodel import select
from models import Model, HealthRecord
from adapters.base import HealthInfo


class TestRecordPassiveHealth:
    @pytest.mark.asyncio
    async def test_healthy_status(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 200, None, sample_channel.id, "sk-test")

        records = db_session.exec(select(HealthRecord)).all()
        assert len(records) == 1
        assert records[0].status == "healthy"
        assert records[0].response_ms == 200
        assert records[0].is_passive is True

    @pytest.mark.asyncio
    async def test_slow_status(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 1500, None, sample_channel.id, "sk-test")

        records = db_session.exec(select(HealthRecord)).all()
        assert records[0].status == "slow"

    @pytest.mark.asyncio
    async def test_down_status_with_error(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 500, "rate_limited", sample_channel.id, "sk-test")

        records = db_session.exec(select(HealthRecord)).all()
        assert records[0].status == "down"
        assert records[0].error_code == "rate_limited"

    @pytest.mark.asyncio
    async def test_error_takes_precedence(self, db_session, sample_model, sample_channel):
        """Even with slow response time, error_code → 'down'."""
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 3000, "timeout", sample_channel.id, "sk-test")

        records = db_session.exec(select(HealthRecord)).all()
        assert records[0].status == "down"

    @pytest.mark.asyncio
    async def test_model_status_updated(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 200, None, sample_channel.id, "sk-test")

        db_session.refresh(sample_model)
        assert sample_model.health_status == "healthy"
        assert sample_model.last_response_ms == 200
        assert sample_model.last_real_call_at is not None

    @pytest.mark.asyncio
    async def test_timestamp_updates(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 200, None, sample_channel.id, "sk-test")

        db_session.refresh(sample_model)
        assert sample_model.last_checked_at is not None
        assert sample_model.last_real_call_at is not None


class TestActiveProbe:
    """active_probe writes the adapter's HealthInfo onto the Model row. These
    tests pin the contract that transient failures (now reported as 'slow' by
    every adapter) never get written as 'down', so a single blip can't eject
    a model from the routing pool."""

    @pytest.mark.asyncio
    async def test_healthy_probe_updates_status(self, db_session, sample_model, sample_channel):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="healthy", response_ms=150))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "healthy"
        assert sample_model.last_response_ms == 150
        assert sample_model.last_checked_at is not None

    @pytest.mark.asyncio
    async def test_transient_server_error_stays_slow(self, db_session, sample_model, sample_channel):
        # A 5xx is reported as slow by the adapter; active_probe must record
        # it as slow, NOT down — otherwise the model is dropped from the pool.
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="slow", response_ms=300, error_code="server_error"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "slow"
        assert sample_model.last_response_ms == 300

    @pytest.mark.asyncio
    async def test_transient_network_error_stays_slow(self, db_session, sample_model, sample_channel):
        # network_error is slow now; the old active_probe hack that special-
        # cased it has been removed, so it must be written through normally.
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="slow", response_ms=0, error_code="network_error"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "slow"

    @pytest.mark.asyncio
    async def test_deterministic_failure_still_marked_down(self, db_session, sample_model, sample_channel):
        # Deterministic failures (auth/not_found/empty) stay down — only the
        # transient ones were changed.
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="down", response_ms=120, error_code="auth_failed"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "down"

    @pytest.mark.asyncio
    async def test_rate_limit_probe_sets_cooldown(self, db_session, sample_model, sample_channel):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="slow", response_ms=250, error_code="rate_limited"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "rate_limited"
        assert sample_model.rate_limited_until is not None
        records = db_session.exec(select(HealthRecord)).all()
        assert records[0].status == "rate_limited"

    @pytest.mark.asyncio
    async def test_records_health_record(self, db_session, sample_model, sample_channel):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="healthy", response_ms=120))):
            await active_probe(sample_model, "sk-test")
        records = db_session.exec(select(HealthRecord)).all()
        assert len(records) == 1
        assert records[0].status == "healthy"
        assert records[0].is_passive is False
