import pytest
from datetime import datetime, timezone
from sqlmodel import select
from models import Model, HealthRecord


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
