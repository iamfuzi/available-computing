import pytest
import json
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
    async def test_single_error_demotes_to_slow_not_down(self, db_session, sample_model, sample_channel):
        """A single transient error must not evict the model — it stays routable as 'slow'."""
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 500, "server_error", sample_channel.id, "sk-test")

        records = db_session.exec(select(HealthRecord)).all()
        # The HealthRecord itself records the demoted status, not "down".
        assert records[0].status == "slow"
        assert records[0].error_code == "server_error"
        db_session.refresh(sample_model)
        assert sample_model.health_status == "slow"
        assert sample_model.consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_error_with_slow_response_still_slow(self, db_session, sample_model, sample_channel):
        """Even with slow response time + error, single failure stays 'slow'."""
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 3000, "timeout", sample_channel.id, "sk-test")

        records = db_session.exec(select(HealthRecord)).all()
        assert records[0].status == "slow"

    @pytest.mark.asyncio
    async def test_consecutive_errors_reach_down(self, db_session, sample_model, sample_channel):
        """Only PASSIVE_ERROR_DOWN_THRESHOLD errors in a row mark the model 'down'."""
        from services.health import record_passive_health
        from config import PASSIVE_ERROR_DOWN_THRESHOLD

        for _ in range(PASSIVE_ERROR_DOWN_THRESHOLD - 1):
            await record_passive_health(sample_model.id, 500, "server_error", sample_channel.id, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "slow"  # below threshold, still routable

        # The threshold-th error tips it over.
        await record_passive_health(sample_model.id, 500, "server_error", sample_channel.id, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.health_status == "down"
        assert sample_model.consecutive_errors == PASSIVE_ERROR_DOWN_THRESHOLD

    @pytest.mark.asyncio
    async def test_success_resets_error_counter(self, db_session, sample_model, sample_channel):
        """Any success clears the consecutive-error count and restores health."""
        from services.health import record_passive_health
        await record_passive_health(sample_model.id, 500, "server_error", sample_channel.id, "sk-test")
        await record_passive_health(sample_model.id, 500, "server_error", sample_channel.id, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.consecutive_errors == 2

        await record_passive_health(sample_model.id, 200, None, sample_channel.id, "sk-test")
        db_session.refresh(sample_model)
        assert sample_model.consecutive_errors == 0
        assert sample_model.health_status == "healthy"

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

    @pytest.mark.asyncio
    async def test_success_creates_verification_evidence(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health

        await record_passive_health(sample_model.id, 200, None, sample_channel.id, "sk-test")

        db_session.refresh(sample_model)
        record = db_session.exec(select(HealthRecord)).one()
        assert sample_model.last_verified_at is not None
        assert sample_model.verification_method == "passive"
        assert record.verification_method == "passive"
        assert record.http_status == 200
        assert record.check_run_id is not None

    @pytest.mark.asyncio
    async def test_failure_does_not_refresh_last_verified_at(self, db_session, sample_model, sample_channel):
        from services.health import record_passive_health

        old_verified_at = datetime.now(timezone.utc) - timedelta(days=10)
        sample_model.last_verified_at = old_verified_at
        sample_model.verification_method = "active_heartbeat"
        db_session.add(sample_model)
        db_session.commit()

        await record_passive_health(sample_model.id, 500, "network_error", sample_channel.id, "sk-test")

        db_session.refresh(sample_model)
        assert sample_model.last_verified_at.replace(tzinfo=timezone.utc) == old_verified_at
        assert sample_model.verification_method == "active_heartbeat"


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
        # transient ones were changed. validate_key is mocked as failing so
        # the channel-flip path is exercised without network access.
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="down", response_ms=120, error_code="auth_failed"))), \
             patch("adapters.openrouter.OpenRouterAdapter.validate_key",
                   new=AsyncMock(side_effect=ValueError("Invalid API key"))):
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

    @pytest.mark.asyncio
    async def test_probe_method_is_saved_on_success(self, db_session, sample_model, sample_channel):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="healthy", response_ms=120))):
            await active_probe(sample_model, "sk-test", "active_baseline")

        db_session.refresh(sample_model)
        record = db_session.exec(select(HealthRecord)).one()
        assert sample_model.last_verified_at is not None
        assert sample_model.verification_method == "active_baseline"
        assert record.verification_method == "active_baseline"
        assert record.http_status == 200

    @pytest.mark.asyncio
    async def test_transient_probe_failure_does_not_verify_model(self, db_session, sample_model, sample_channel):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="slow", response_ms=0, error_code="network_error"))):
            await active_probe(sample_model, "sk-test", "active_heartbeat")

        db_session.refresh(sample_model)
        assert sample_model.last_verified_at is None
        assert sample_model.verification_method is None

    @pytest.mark.asyncio
    async def test_auth_probe_marks_channel_key_invalid(self, db_session, sample_model, sample_channel):
        from services.health import active_probe
        # Since the key-invalid confirmation change, the channel only flips when
        # the key-level validate_key also fails (mocked here).
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="down", response_ms=30, error_code="auth_failed"))), \
             patch("adapters.openrouter.OpenRouterAdapter.validate_key",
                   new=AsyncMock(side_effect=ValueError("Invalid API key"))):
            await active_probe(sample_model, "sk-test")

        db_session.refresh(sample_channel)
        assert sample_channel.status == "key_invalid"
        assert sample_channel.status_reason == "active_probe_auth_failed"

    @pytest.mark.asyncio
    async def test_anonymous_provider_auth_failure_does_not_invalidate_channel(
        self, db_session, sample_model, sample_channel
    ):
        from services.health import active_probe

        sample_channel.provider_type = "kilo-code"
        db_session.add(sample_channel)
        db_session.commit()
        with patch(
            "adapters.declarative.DeclarativeAdapter.health_check",
            new=AsyncMock(return_value=HealthInfo(status="down", response_ms=30, error_code="auth_failed")),
        ):
            await active_probe(sample_model, "")

        db_session.refresh(sample_channel)
        db_session.refresh(sample_model)
        assert sample_model.health_status == "down"
        assert sample_channel.status == "active"
        assert sample_channel.status_reason is None


class TestHeartbeatBudget:
    def test_unknown_rpd_skips_heartbeat(self, sample_model):
        from services.health import _channel_heartbeat_budget
        sample_model.rate_limit = None
        assert _channel_heartbeat_budget([sample_model]) == 0

    def test_low_rpd_skips_heartbeat(self, sample_model):
        from services.health import _channel_heartbeat_budget
        sample_model.rate_limit = json.dumps({"rpd": 99})
        assert _channel_heartbeat_budget([sample_model]) == 0

    def test_budget_is_one_percent_and_preserves_reserve(self, sample_model):
        from services.health import _channel_heartbeat_budget
        sample_model.rate_limit = json.dumps({"rpd": 10_000})
        assert _channel_heartbeat_budget([sample_model]) == 100

    def test_provider_budget_uses_most_conservative_model_limit(self, sample_model):
        from services.health import _channel_heartbeat_budget
        sample_model.rate_limit = json.dumps({"rpd": 10_000})
        other = Model(rate_limit=json.dumps({"rpd": 100}))
        assert _channel_heartbeat_budget([sample_model, other]) == 1

    def test_only_long_idle_model_is_candidate(self, sample_model):
        from services.health import _is_heartbeat_candidate
        now = datetime.now(timezone.utc)
        sample_model.last_verified_at = now - timedelta(days=4)
        assert _is_heartbeat_candidate(sample_model, now) is True
        sample_model.last_real_call_at = now - timedelta(hours=2)
        assert _is_heartbeat_candidate(sample_model, now) is False

    @pytest.mark.asyncio
    async def test_sweep_probes_oldest_model_within_budget(self, db_session, sample_model, sample_channel):
        from services.health import probe_all_stale_models
        sample_model.rate_limit = json.dumps({"rpd": 100})
        sample_model.last_verified_at = datetime.now(timezone.utc) - timedelta(days=4)
        db_session.add(sample_model)
        db_session.commit()

        with patch("services.health.active_probe", new=AsyncMock()) as probe, \
             patch("services.health.asyncio.sleep", new=AsyncMock()):
            await probe_all_stale_models()

        probe.assert_awaited_once()
        assert probe.await_args.args[0].id == sample_model.id
        assert probe.await_args.args[2] == "active_heartbeat"

    @pytest.mark.asyncio
    async def test_sweep_respects_used_daily_budget(self, db_session, sample_model, sample_channel):
        from services.health import probe_all_stale_models
        sample_model.rate_limit = json.dumps({"rpd": 100})
        sample_model.last_verified_at = datetime.now(timezone.utc) - timedelta(days=4)
        db_session.add(sample_model)
        db_session.add(HealthRecord(
            model_id=sample_model.id,
            status="healthy",
            verification_method="active_heartbeat",
            is_passive=False,
        ))
        db_session.commit()

        with patch("services.health.active_probe", new=AsyncMock()) as probe:
            await probe_all_stale_models()

        probe.assert_not_awaited()


class TestEventRecheck:
    def test_trigger_deduplicates_pending_model(self, sample_model):
        from services.event_recheck import trigger_event_recheck

        with patch("services.event_recheck._schedule_attempt") as schedule:
            first = trigger_event_recheck(sample_model.id, "rate_limited")
            second = trigger_event_recheck(sample_model.id, "rate_limited")

        assert first == second
        schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_recheck_stops_batch(self, sample_model):
        from services.event_recheck import _pending_rechecks, run_event_recheck

        run_id = "run-success"
        _pending_rechecks[sample_model.id] = run_id
        with patch(
            "services.health.active_probe",
            new=AsyncMock(return_value=HealthInfo(status="healthy", response_ms=50)),
        ), patch("services.event_recheck._schedule_attempt") as schedule:
            await run_event_recheck(
                model_id=sample_model.id,
                trigger_reason="rate_limited",
                check_run_id=run_id,
                attempt=1,
                window_started_at=datetime.now(timezone.utc).isoformat(),
            )

        assert sample_model.id not in _pending_rechecks
        schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_three_rate_limit_rechecks_confirm_quota_policy(
        self, db_session, sample_model, sample_channel
    ):
        from services.event_recheck import _pending_rechecks, run_event_recheck

        run_id = "run-rate-limit"
        started_at = datetime.now(timezone.utc).isoformat()
        _pending_rechecks[sample_model.id] = run_id
        limited = HealthInfo(status="slow", response_ms=20, error_code="rate_limited")
        with patch(
            "adapters.openrouter.OpenRouterAdapter.health_check",
            new=AsyncMock(return_value=limited),
        ), patch("services.event_recheck._schedule_attempt"):
            for attempt in (1, 2, 3):
                await run_event_recheck(
                    model_id=sample_model.id,
                    trigger_reason="rate_limited",
                    check_run_id=run_id,
                    attempt=attempt,
                    window_started_at=started_at,
                )

        db_session.refresh(sample_model)
        records = db_session.exec(
            select(HealthRecord).where(HealthRecord.check_run_id == run_id)
        ).all()
        assert len(records) == 3
        assert sample_model.is_free is True
        assert sample_model.free_type == "quota"
        assert sample_model.free_source == "event_recheck"
        assert sample_model.id not in _pending_rechecks

    def test_three_402_rechecks_remove_model_pending_review(self, db_session, sample_model):
        from services.event_recheck import _apply_confirmed_change

        run_id = "run-payment-required"
        for _ in range(3):
            db_session.add(HealthRecord(
                model_id=sample_model.id,
                status="down",
                error_code="server_error",
                verification_method="active_event_triggered",
                check_run_id=run_id,
                is_passive=False,
            ))
        db_session.commit()

        _apply_confirmed_change(sample_model.id, "upstream_402", run_id)

        db_session.refresh(sample_model)
        assert sample_model.is_free is None
        assert sample_model.free_type == "billing_suspect"
        assert sample_model.free_source == "event_recheck"


class TestAuthFailedKeyConfirmation:
    """A model-level 401/403 is not proof the channel key died — region locks
    and retired free variants produce auth-shaped errors with a valid key.
    Only a failing key-level validate_key may flip the channel to
    key_invalid (and raise the critical notification)."""

    @pytest.mark.asyncio
    async def test_auth_failed_with_valid_key_keeps_channel_active(
        self, db_session, sample_model, sample_channel
    ):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="down", response_ms=100, error_code="auth_failed"))), \
             patch("adapters.openrouter.OpenRouterAdapter.validate_key",
                   new=AsyncMock(return_value=None)):  # key-level check passes
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_channel)
        assert sample_channel.status == "active"

    @pytest.mark.asyncio
    async def test_auth_failed_with_failing_key_flips_channel(
        self, db_session, sample_model, sample_channel
    ):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="down", response_ms=100, error_code="auth_failed"))), \
             patch("adapters.openrouter.OpenRouterAdapter.validate_key",
                   new=AsyncMock(side_effect=ValueError("Invalid API key"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_channel)
        assert sample_channel.status == "key_invalid"
        assert sample_channel.status_reason == "active_probe_auth_failed"

    @pytest.mark.asyncio
    async def test_access_restricted_never_flips_channel(
        self, db_session, sample_model, sample_channel
    ):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="down", response_ms=100, error_code="access_restricted"))), \
             patch("adapters.openrouter.OpenRouterAdapter.validate_key",
                   new=AsyncMock(side_effect=AssertionError("must not be called"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_channel)
        assert sample_channel.status == "active"
        db_session.refresh(sample_model)
        assert sample_model.health_status == "down"
        record = db_session.exec(select(HealthRecord)).one()
        assert record.failure_reason == "access_restricted"
        assert record.http_status == 403

    @pytest.mark.asyncio
    async def test_upstream_rate_limited_probe_keeps_model_routable(
        self, db_session, sample_model, sample_channel
    ):
        from services.health import active_probe
        with patch("adapters.openrouter.OpenRouterAdapter.health_check",
                   new=AsyncMock(return_value=HealthInfo(status="slow", response_ms=250, error_code="upstream_rate_limited"))):
            await active_probe(sample_model, "sk-test")
        db_session.refresh(sample_model)
        # Upstream overload is not our quota: no cooldown, model stays routable.
        assert sample_model.health_status == "slow"
        assert sample_model.rate_limited_until is None
        record = db_session.exec(select(HealthRecord)).one()
        assert record.failure_reason == "upstream_rate_limited"
        assert record.http_status == 429


class TestProbeDailyBudget:
    """Setting probe_daily_budget:<channel_id> caps ALL non-passive probes per
    UTC day. Guards providers whose free tier bills every model request
    against a tiny global daily quota (OpenRouter: 50/day, failures included)."""

    def test_override_reading(self, db_session, sample_channel):
        from models import Setting
        from services.health import _probe_daily_budget_override
        assert _probe_daily_budget_override(db_session, sample_channel.id) is None
        db_session.add(Setting(key=f"probe_daily_budget:{sample_channel.id}", value="6"))
        db_session.commit()
        assert _probe_daily_budget_override(db_session, sample_channel.id) == 6

    @pytest.mark.parametrize("value", ["0", "-3", "abc"])
    def test_override_ignores_non_positive_or_malformed(self, db_session, sample_channel, value):
        from models import Setting
        from services.health import _probe_daily_budget_override
        db_session.add(Setting(key=f"probe_daily_budget:{sample_channel.id}", value=value))
        db_session.commit()
        assert _probe_daily_budget_override(db_session, sample_channel.id) is None

    def test_active_probe_count_excludes_passive(self, db_session, sample_model, sample_channel):
        from services.health import _active_probe_count_today
        now = datetime.now(timezone.utc)
        for i in range(3):
            db_session.add(HealthRecord(
                model_id=sample_model.id, status="slow", response_ms=1,
                is_passive=False, verification_method="active_recheck", checked_at=now,
            ))
        db_session.add(HealthRecord(
            model_id=sample_model.id, status="healthy", response_ms=1,
            is_passive=True, verification_method="passive", checked_at=now,
        ))
        db_session.commit()
        assert _active_probe_count_today(db_session, [sample_model.id], now) == 3

    @pytest.mark.asyncio
    async def test_reprobe_down_respects_budget(self, db_session, sample_channel):
        from models import Setting
        from services.health import reprobe_down_models

        for i in range(5):
            db_session.add(Model(
                id=f"mdl-d{i}", channel_id=sample_channel.id, model_id=f"down-{i}",
                display_name=f"down-{i}", category="text", is_free=True,
                health_status="down", is_active=True,
                last_checked_at=datetime.now(timezone.utc) - timedelta(hours=6 - i),
            ))
        db_session.add(Setting(key=f"probe_daily_budget:{sample_channel.id}", value="2"))
        db_session.commit()
        # One non-passive probe already happened today → only 1 slot remains.
        db_session.add(HealthRecord(
            model_id="mdl-d0", status="down", response_ms=1, is_passive=False,
            verification_method="active_baseline", checked_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        with patch("services.health.active_probe", new=AsyncMock()) as mock_probe:
            await reprobe_down_models()
        assert mock_probe.await_count == 1
        probed = mock_probe.await_args_list[0].args[0]
        assert probed.id == "mdl-d0"  # oldest-checked first across the backlog

    @pytest.mark.asyncio
    async def test_reprobe_down_budget_exhausted_skips_channel(self, db_session, sample_channel):
        from models import Setting
        from services.health import reprobe_down_models

        db_session.add(Model(
            id="mdl-x", channel_id=sample_channel.id, model_id="down-x",
            display_name="down-x", category="text", is_free=True,
            health_status="down", is_active=True,
        ))
        db_session.add(Setting(key=f"probe_daily_budget:{sample_channel.id}", value="2"))
        db_session.commit()
        for i in range(2):
            db_session.add(HealthRecord(
                model_id="mdl-x", status="down", response_ms=1, is_passive=False,
                verification_method="active_recheck", checked_at=datetime.now(timezone.utc),
            ))
        db_session.commit()

        with patch("services.health.active_probe", new=AsyncMock()) as mock_probe:
            n = await reprobe_down_models()
        assert mock_probe.await_count == 0
        assert n == 0

    @pytest.mark.asyncio
    async def test_discovery_baseline_respects_budget_but_manual_bypasses(
        self, db_session, sample_channel
    ):
        from models import Setting
        from services.health import probe_channel_models

        for i in range(3):
            db_session.add(Model(
                id=f"mdl-u{i}", channel_id=sample_channel.id, model_id=f"unverified-{i}",
                display_name=f"unverified-{i}", category="text", is_free=True,
                health_status="unknown", is_active=True,
            ))
        db_session.add(Setting(key=f"probe_daily_budget:{sample_channel.id}", value="3"))
        db_session.commit()
        db_session.add(HealthRecord(
            model_id="mdl-u0", status="slow", response_ms=1, is_passive=False,
            verification_method="active_heartbeat", checked_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        with patch("services.health.active_probe", new=AsyncMock()) as mock_probe:
            await probe_channel_models(sample_channel.id, "active_baseline", only_unverified=True)
        assert mock_probe.await_count == 2  # 3 budget - 1 used today

        with patch("services.health.active_probe", new=AsyncMock()) as mock_probe:
            await probe_channel_models(sample_channel.id, "manual")
        assert mock_probe.await_count == 3  # explicit admin action bypasses
