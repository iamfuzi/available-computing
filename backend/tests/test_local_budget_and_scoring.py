"""Tests for the model_rpm Setting override and traffic-vs-probe scoring split.

Background (2026-10-02): siliconflow's embeddings/rerank models never send
rate-limit headers, so the PROXY_DEFAULT_MODEL_RPM floor pinned them at 30 RPM
and hotspot's rerank bursts were shed locally (440x local_model_budget_exceeded
in 26 minutes) while the provider had real headroom. The Setting
``model_rpm:<model_id>`` lets the admin raise a single model without touching
the floor for everyone. Separately, route scoring ranked on last_response_ms,
which active probes overwrite with tiny-payload latencies — every probe sweep
made freshly probed models jump their health bucket for hours.
"""
from datetime import datetime, timedelta, timezone

import pytest


def _make_model(db_session, suffix="a", **overrides):
    # Backend imports stay INSIDE the functions: this suite's conftest swaps
    # database.engine for an in-memory engine in a session fixture, which runs
    # after collection. A module-level `from database import engine` (directly
    # or via api.proxy) would bind the real on-disk engine first and break
    # every other test file's passive-health writes.
    from models import Model
    model = Model(
        id=f"mdl-mr-{suffix}",
        channel_id="ch-001",
        model_id=f"mrm-{suffix}",
        category="text",
        is_free=True,
        is_active=True,
        health_status="healthy",
        **overrides,
    )
    db_session.add(model)
    db_session.commit()
    return model


def _add_passive(db_session, model_id, ms=None, status="healthy", minutes_ago=0):
    from models import HealthRecord
    db_session.add(HealthRecord(
        model_id=model_id,
        status=status,
        response_ms=ms,
        is_passive=True,
        checked_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    ))
    db_session.commit()


# ── model_rpm Setting override ────────────────────────────────────────────

class TestModelRpmSettingOverride:
    def test_setting_wins_over_observed_and_floor(self, db_session, sample_channel):
        model = _make_model(db_session, rate_limit='{"rpm": 30}')
        from sqlmodel import Session
        import database
        from models import Setting
        from api.proxy import _effective_model_rpm
        db_session.add(Setting(key="model_rpm:mrm-a", value="300"))
        db_session.commit()
        with Session(database.engine) as s:
            assert _effective_model_rpm(s, model) == 300

    def test_zero_or_invalid_setting_falls_through(self, db_session, sample_channel):
        from sqlmodel import Session
        import database
        from models import Setting
        from api.proxy import _effective_model_rpm
        model = _make_model(db_session, suffix="b", rate_limit='{"rpm": 30}')
        db_session.add(Setting(key="model_rpm:mrm-b", value="0"))
        db_session.commit()
        with Session(database.engine) as s:
            assert _effective_model_rpm(s, model) == 30

    def test_floor_applies_without_setting_or_observed(self, db_session, sample_channel, monkeypatch):
        from sqlmodel import Session
        import database
        import api.proxy as proxy_mod
        from api.proxy import _effective_model_rpm
        model = _make_model(db_session, suffix="c", rate_limit=None)
        monkeypatch.setattr(proxy_mod, "PROXY_DEFAULT_MODEL_RPM", 30)
        with Session(database.engine) as s:
            assert _effective_model_rpm(s, model) == 30
        monkeypatch.setattr(proxy_mod, "PROXY_DEFAULT_MODEL_RPM", 0)
        with Session(database.engine) as s:
            assert _effective_model_rpm(s, model) is None

    def test_budget_uses_setting_boundary(self, db_session, sample_channel):
        """Setting model_rpm=2: the 2nd call in the window passes, 3rd sheds."""
        from sqlmodel import Session
        import database
        from models import Setting
        from api.proxy import ModelBudgetExceeded, _check_model_budget
        model = _make_model(db_session, suffix="d", rate_limit=None)
        db_session.add(Setting(key="model_rpm:mrm-d", value="2"))
        db_session.commit()
        _add_passive(db_session, model.id, ms=100)
        with Session(database.engine) as s:
            _check_model_budget(model, s)  # 1 < 2 → passes
        _add_passive(db_session, model.id, ms=100)
        with Session(database.engine) as s:
            with pytest.raises(ModelBudgetExceeded) as exc:
                _check_model_budget(model, s)
            assert exc.value.reason == "local_rpm_exceeded"


# ── scoring: real-traffic latency vs probe latency ────────────────────────

class TestRouteScoreLatency:
    def test_passive_median_beats_probe_point_value(self, db_session, sample_channel):
        """A probe set last_response_ms=400; real traffic came back ~12s.
        The rank key must use the traffic median, not the probe value."""
        from services.router.scoring import route_score_key
        model = _make_model(db_session, suffix="s1", last_response_ms=400)
        for i, ms in enumerate((10000, 12000, 14000)):
            _add_passive(db_session, model.id, ms=ms, minutes_ago=i + 1)
        key = route_score_key(model, db_session)
        assert key[3] == 12000

    def test_falls_back_to_last_response_ms_without_traffic(self, db_session, sample_channel):
        """Never-called model: probe latency is all we know — keep using it."""
        from services.router.scoring import route_score_key
        model = _make_model(db_session, suffix="s2", last_response_ms=400)
        key = route_score_key(model, db_session)
        assert key[3] == 400

    def test_active_probe_records_do_not_pollute_latency(self, db_session, sample_channel):
        """Probe sweeps record fast active records; they must not dilute the
        passive median after a real-traffic slowdown."""
        from models import HealthRecord
        from services.router.scoring import route_score_key
        model = _make_model(db_session, suffix="s3", last_response_ms=300)
        for i in range(15):
            db_session.add(HealthRecord(
                model_id=model.id,
                status="healthy",
                response_ms=300,
                is_passive=False,
                checked_at=datetime.now(timezone.utc) - timedelta(minutes=i + 1),
            ))
        db_session.commit()
        _add_passive(db_session, model.id, ms=12000, minutes_ago=0)
        key = route_score_key(model, db_session)
        assert key[3] == 12000

    def test_slow_probed_model_ranks_behind_fast_traffic_model(self, db_session, sample_channel):
        """The observed failure: after a probe sweep the just-probed heavy model
        out-ranked a real-traffic-proven fast model. With passive medians the
        order flips back (same health bucket, both routable)."""
        from services.router.scoring import route_score_key
        probed = _make_model(db_session, suffix="s4", last_response_ms=400)
        _add_passive(db_session, probed.id, ms=12000, minutes_ago=1)
        proven = _make_model(db_session, suffix="s5", last_response_ms=6000)
        _add_passive(db_session, proven.id, ms=800, minutes_ago=1)
        assert route_score_key(probed, db_session) > route_score_key(proven, db_session)
