"""Tests for the routing-upgrade observability layer: request-id middleware,
standardized error bodies, and the diagnostic response headers.

These cover stage 2 of the multi-provider smart-routing upgrade:
- Every response (success and error) carries X-AC-Request-ID.
- A caller-supplied request id propagates end-to-end.
- Error bodies include the standardized ``type``/``code``/``retryable``/
  ``scope``/``request_id`` fields.
- ``Retry-After`` (RFC 7231) is emitted alongside the legacy X-AC-Retry-After.
"""
import pytest

from services import errors


# ── Unit: error builder ───────────────────────────────────────────────────


class TestMakeAcError:
    def test_body_has_standard_fields(self):
        resp = errors.make_ac_error(
            503,
            "No candidate satisfied the routing policy",
            "routing_exhausted",
            "all_candidates_unavailable",
            request_id="ac_req_abc",
            attempted_models=["provider-a/model-x"],
        )
        body = resp.body.decode()
        import json
        err = json.loads(body)["error"]
        assert err["type"] == "routing_exhausted"
        assert err["code"] == "all_candidates_unavailable"
        assert err["message"] == "No candidate satisfied the routing policy"
        assert err["retryable"] is True  # routing_exhausted is retryable
        assert err["scope"] == "routing_profile"
        assert err["request_id"] == "ac_req_abc"
        assert err["attempted_models"] == ["provider-a/model-x"]

    def test_retryable_inferred_from_type(self):
        # rate_limited → retryable
        r1 = errors.make_ac_error(429, "x", "rate_limited", "c")
        import json
        assert json.loads(r1.body.decode())["error"]["retryable"] is True
        # invalid_request_error → not retryable
        r2 = errors.make_ac_error(404, "x", "invalid_request_error", "c")
        assert json.loads(r2.body.decode())["error"]["retryable"] is False

    def test_retryable_override(self):
        r = errors.make_ac_error(503, "x", "service_unavailable", "c", retryable=False)
        import json
        # caller override wins even though service_unavailable defaults retryable
        assert json.loads(r.body.decode())["error"]["retryable"] is False

    def test_retry_after_writes_standard_header(self):
        r = errors.make_ac_error(
            429, "x", "rate_limited", "c", retry_after=60
        )
        assert r.headers["Retry-After"] == "60"
        assert r.headers["X-AC-Retry-After"] == "60"

    def test_request_id_in_header_and_body(self):
        r = errors.make_ac_error(
            404, "x", "invalid_request_error", "c", request_id="ac_req_zz"
        )
        assert r.headers["X-AC-Request-ID"] == "ac_req_zz"
        import json
        assert json.loads(r.body.decode())["error"]["request_id"] == "ac_req_zz"

    def test_attempt_count_header(self):
        r = errors.make_ac_error(
            503, "x", "routing_exhausted", "c",
            attempted_models=["a", "b", "c"],
        )
        assert r.headers["X-AC-Attempt-Count"] == "3"
        assert r.headers["X-AC-Attempted-Models"] == "a,b,c"

    def test_scope_defaults_by_type(self):
        r = errors.make_ac_error(503, "x", "service_unavailable", "c")
        import json
        assert json.loads(r.body.decode())["error"]["scope"] == "upstream"

    def test_no_scope_for_legacy_types_without_default(self):
        # invalid_request_error has no default scope mapping
        r = errors.make_ac_error(400, "x", "invalid_request_error", "c")
        import json
        assert "scope" not in json.loads(r.body.decode())["error"]


# ── Integration: middleware + chat endpoint ───────────────────────────────


class TestRequestIdMiddleware:
    @pytest.mark.asyncio
    async def test_success_response_carries_request_id(self, app_client, auth_headers, sample_model, sample_channel):
        """A successful chat completion must echo X-AC-Request-ID."""
        from unittest.mock import patch, MagicMock, AsyncMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "chatcmpl-1", "choices": [{"message": {"content": "hi"}}]}
        mock_response.headers = {}

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_response)
        with patch("httpx.AsyncClient", return_value=mock_cm):
            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "test-model-free", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.headers.get("X-AC-Request-ID", "").startswith("ac_req_")

    @pytest.mark.asyncio
    async def test_caller_supplied_request_id_propagates(self, app_client, auth_headers):
        """When the caller sends X-AC-Request-ID, it is echoed back unchanged."""
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers={**auth_headers, "X-AC-Request-ID": "ac_req_caller_123"},
        )
        assert resp.status_code == 404
        assert resp.headers["X-AC-Request-ID"] == "ac_req_caller_123"

    @pytest.mark.asyncio
    async def test_error_body_contains_request_id(self, app_client, auth_headers):
        """The JSON error body includes request_id for programmatic correlation."""
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        body = resp.json()
        assert body["error"]["request_id"].startswith("ac_req_")
        # the header and body must agree
        assert body["error"]["request_id"] == resp.headers["X-AC-Request-ID"]


class TestStandardErrorBody:
    @pytest.mark.asyncio
    async def test_model_not_found_has_standard_fields(self, app_client, auth_headers):
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        err = resp.json()["error"]
        assert err["type"] == "invalid_request_error"
        assert err["code"] == "model_not_found"
        assert "retryable" in err
        assert err["retryable"] is False
        assert err["param"] == "model"

    @pytest.mark.asyncio
    async def test_status_endpoint_has_request_id(self, app_client, auth_headers):
        """The /v1/ac/status diagnostics endpoint also gets a request id."""
        resp = await app_client.get("/v1/ac/status", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers.get("X-AC-Request-ID", "").startswith("ac_req_")


# ── Stage 3: routing profiles ─────────────────────────────────────────────


class TestRoutingProfiles:
    def test_hotspot_classifier_profile_loads(self):
        """The example profile ships and parses with the documented constraints."""
        from services.router import load_profile, list_profiles

        assert "hotspot-classifier" in list_profiles()
        p = load_profile("hotspot-classifier")
        assert p is not None
        assert p.task == "classification"
        assert p.objective == "latency"
        assert p.free_only is True
        assert "google" in p.provider_denylist
        assert "gemini" in p.provider_denylist
        assert "gemini" in p.model_deny_patterns
        assert "reasoning" in p.model_deny_patterns
        assert p.max_attempts == 3
        assert p.max_attempts_per_provider == 1

    def test_unknown_profile_returns_none(self):
        from services.router import load_profile
        assert load_profile("does-not-exist") is None

    def test_provider_denied_check(self):
        from services.router import load_profile
        p = load_profile("hotspot-classifier")
        assert p.provider_denied("google") is True
        assert p.provider_denied("groq") is False

    def test_model_denied_check_is_substring_case_insensitive(self):
        from services.router import load_profile
        p = load_profile("hotspot-classifier")
        assert p.model_denied("google/gemini-1.5-pro") is True
        assert p.model_denied("some-reasoning-model") is True
        assert p.model_denied("meta-llama/llama-3.3-70b") is False

    def test_profile_merges_into_effective_policy(self):
        """effective_routing_policy unions the profile's denylists."""
        from services.router import effective_routing_policy, RoutingPolicy, load_profile

        p = load_profile("hotspot-classifier")
        pol = effective_routing_policy(None, RoutingPolicy(profile="hotspot-classifier"), p)
        assert "google" in pol.provider_blacklist
        assert "gemini" in pol.provider_blacklist
        assert "gemini" in pol.model_deny_patterns
        assert pol.max_attempts == 3
        assert pol.max_attempts_per_provider == 1
        assert pol.profile_name == "hotspot-classifier"

    def test_profile_blacklist_unions_with_request_exclude(self):
        """A request's exclude list adds to (never subtracts from) the profile."""
        from services.router import effective_routing_policy, RoutingPolicy, load_profile

        p = load_profile("hotspot-classifier")
        pol = effective_routing_policy(
            None,
            RoutingPolicy(profile="hotspot-classifier", exclude=["groq"]),
            p,
        )
        # both the profile's google and the request's groq are denied
        assert "google" in pol.provider_blacklist
        assert "groq" in pol.provider_blacklist

    def test_no_profile_keeps_legacy_behaviour(self):
        """Without a profile, model_deny_patterns/max_attempts stay empty/None."""
        from services.router import effective_routing_policy

        pol = effective_routing_policy(None)
        assert pol.model_deny_patterns == frozenset()
        assert pol.max_attempts is None
        assert pol.max_attempts_per_provider is None
        assert pol.profile_name is None


class TestProfileAuthorization:
    def test_open_by_default_when_no_allowlist(self):
        """An ApiKey with no allowed_profiles may use any profile (personal default)."""
        import hashlib
        from models import ApiKey
        from services.router import is_profile_authorized

        key = ApiKey(
            name="k", key_hash=hashlib.sha256(b"x").hexdigest(),
            key_prefix="ac_x", key_encrypted="", is_active=True,
            allowed_profiles=None,
        )
        assert is_profile_authorized(key, "hotspot-classifier") is True

    def test_explicit_allowlist_grants_listed_profile(self):
        import hashlib, json
        from models import ApiKey
        from services.router import is_profile_authorized

        key = ApiKey(
            name="k", key_hash=hashlib.sha256(b"x").hexdigest(),
            key_prefix="ac_x", key_encrypted="", is_active=True,
            allowed_profiles=json.dumps(["hotspot-classifier"]),
        )
        assert is_profile_authorized(key, "hotspot-classifier") is True

    def test_explicit_allowlist_denies_unlisted_profile(self):
        import hashlib, json
        from models import ApiKey
        from services.router import is_profile_authorized

        key = ApiKey(
            name="k", key_hash=hashlib.sha256(b"x").hexdigest(),
            key_prefix="ac_x", key_encrypted="", is_active=True,
            allowed_profiles=json.dumps(["other-profile"]),
        )
        assert is_profile_authorized(key, "hotspot-classifier") is False

    def test_jwt_admin_bypasses_profile_authorization(self):
        """Admin/JWT requests (api_key=None) bypass profile checks."""
        from services.router import is_profile_authorized
        assert is_profile_authorized(None, "hotspot-classifier") is True


class TestProfileHttpIntegration:
    @pytest.mark.asyncio
    async def test_unknown_profile_returns_policy_rejected(self, app_client, auth_headers):
        """Requesting a profile that does not exist → 404 policy_rejected."""
        resp = await app_client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model-free",
                "messages": [{"role": "user", "content": "hi"}],
                "routing_policy": {"profile": "no-such-profile"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["type"] == "policy_rejected"
        assert err["code"] == "profile_not_found"

    @pytest.mark.asyncio
    async def test_unauthorized_profile_returns_403(self, app_client, db_session, sample_model, sample_channel):
        """An ApiKey whose allowlist excludes the profile → 403."""
        import hashlib, json
        from models import ApiKey

        raw = "ac_profile_denied"
        key = ApiKey(
            name="denied", key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:8], key_encrypted="", is_active=True,
            allowed_profiles=json.dumps(["some-other-profile"]),
        )
        db_session.add(key)
        db_session.commit()

        resp = await app_client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model-free",
                "messages": [{"role": "user", "content": "hi"}],
                "routing_policy": {"profile": "hotspot-classifier"},
            },
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 403
        err = resp.json()["error"]
        assert err["type"] == "policy_rejected"
        assert err["code"] == "profile_unauthorized"

    @pytest.mark.asyncio
    async def test_authorized_profile_accepted(self, app_client, db_session, sample_model, sample_channel):
        """An ApiKey with an allowlist that includes the profile passes the gate."""
        import hashlib, json
        from unittest.mock import patch, AsyncMock, MagicMock
        from models import ApiKey

        raw = "ac_profile_ok"
        key = ApiKey(
            name="ok", key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:8], key_encrypted="", is_active=True,
            allowed_profiles=json.dumps(["hotspot-classifier"]),
        )
        db_session.add(key)
        db_session.commit()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "x", "choices": [{"message": {"content": "hi"}}]}
        mock_resp.headers = {}
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_cm):
            resp = await app_client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model-free",
                    "messages": [{"role": "user", "content": "hi"}],
                    "routing_policy": {"profile": "hotspot-classifier"},
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        # Should NOT be a 403; it reaches the upstream (200) since the sample
        # model's provider is not on the denylist.
        assert resp.status_code == 200


# ── Stage 4: hard denylist filter + cross-provider fallback ───────────────


class TestHardDenylistFilter:
    def test_model_deny_patterns_drop_matching_candidates(self, db_session):
        """apply_routing_policy drops models whose id matches a deny pattern."""
        from services.router import apply_routing_policy, EffectiveRoutingPolicy
        from models import Model, Channel
        from services.crypto import encrypt, generate_salt

        salt = generate_salt()
        ch = Channel(
            id="ch-deny-test", provider_type="groq", name="groq",
            api_key_enc=encrypt("sk-test", "p", salt), enabled=True,
        )
        db_session.add(ch)
        db_session.commit()
        good = Model(id="m-good", channel_id=ch.id, model_id="llama-3.3-70b",
                     category="text", is_free=True, is_active=True, health_status="healthy")
        bad = Model(id="m-bad", channel_id=ch.id, model_id="some-reasoning-model",
                    category="text", is_free=True, is_active=True, health_status="healthy")
        db_session.add_all([good, bad])
        db_session.commit()

        policy = EffectiveRoutingPolicy(
            provider_whitelist=frozenset(),
            provider_blacklist=frozenset(),
            min_context=None, prefer="latency", fallback_chain=(),
            model_deny_patterns=frozenset({"reasoning"}),
        )
        result = apply_routing_policy([good, bad], policy, db_session)
        ids = {m.model_id for m in result}
        assert "llama-3.3-70b" in ids
        assert "some-reasoning-model" not in ids

    def test_no_deny_patterns_keeps_all(self, db_session, sample_model):
        """Without deny patterns, filtering is a no-op for the model id."""
        from services.router import apply_routing_policy, EffectiveRoutingPolicy

        policy = EffectiveRoutingPolicy(
            provider_whitelist=frozenset(), provider_blacklist=frozenset(),
            min_context=None, prefer="latency", fallback_chain=(),
            model_deny_patterns=frozenset(),
        )
        result = apply_routing_policy([sample_model], policy, db_session)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_denylist_not_bypassable_via_fallback_chain(
        self, app_client, auth_headers, db_session, sample_channel
    ):
        """A denied model cannot re-enter via the fallback_chain field."""
        from models import Model
        from services.crypto import encrypt, generate_salt

        # Create a model that the request will try to fall back to, but which
        # the profile's deny pattern will reject.
        denied = Model(
            id="m-denied", channel_id=sample_channel.id, model_id="gemini-pro-1.5",
            category="text", is_free=True, is_active=True, health_status="healthy",
        )
        primary = Model(
            id="m-primary", channel_id=sample_channel.id, model_id="llama-3.3",
            category="text", is_free=True, is_active=True, health_status="healthy",
        )
        db_session.add_all([denied, primary])
        db_session.commit()

        from unittest.mock import patch, AsyncMock, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "x", "choices": [{"message": {"content": "ok"}}]}
        mock_resp.headers = {}
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_cm) as mocked:
            resp = await app_client.post(
                "/v1/chat/completions",
                json={
                    "model": "nonexistent-primary",
                    "messages": [{"role": "user", "content": "hi"}],
                    "routing_policy": {
                        "profile": "hotspot-classifier",
                        "fallback_chain": ["gemini-pro-1.5"],
                    },
                },
                headers=auth_headers,
            )
        # The gemini fallback must be filtered out by the profile's deny
        # pattern, so the request ends in 404 (no eligible model) rather than
        # routing to gemini.
        assert resp.status_code == 404
        # Confirm the upstream was never called with the denied model.
        called_models = [c.kwargs.get("json", {}).get("model") for c in mocked.return_value.post.call_args_list]
        assert "gemini-pro-1.5" not in called_models


class TestCrossProviderFallback:
    @pytest.mark.asyncio
    async def test_max_attempts_caps_total_upstream_tries(
        self, app_client, auth_headers, db_session, fixed_salt
    ):
        """With max_attempts=3, at most 3 upstream calls are made even if all 500."""
        import base64
        from models import Model, Channel, Setting
        from services.crypto import encrypt
        from unittest.mock import patch, AsyncMock, MagicMock

        db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
        db_session.commit()
        # Five providers, one model each, all healthy — the pool is large.
        channels = []
        models = []
        for i in range(5):
            ch = Channel(
                id=f"ch-cap-{i}", provider_type=["groq","siliconflow","openrouter","zhipu","agnes"][i], name=f"p{i}",
                api_key_enc=encrypt("sk-test-key", "test-admin-password", fixed_salt), enabled=True,
            )
            db_session.add(ch)
            channels.append(ch)
            models.append(Model(
                id=f"m-cap-{i}", channel_id=ch.id, model_id=f"model-g{i}",
                category="text", is_free=True, is_active=True, health_status="healthy",
                last_response_ms=100 + i,
            ))
        db_session.add_all(channels)
        db_session.commit()
        db_session.add_all(models)
        db_session.commit()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "x"}
        mock_resp.headers = {}
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_cm) as mocked:
            resp = await app_client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto:text",
                    "messages": [{"role": "user", "content": "hi"}],
                    "routing_policy": {"profile": "hotspot-classifier"},
                },
                headers=auth_headers,
            )
        # hotspot-classifier sets max_attempts=3, so exactly 3 upstream tries.
        assert resp.status_code == 500
        assert mock_cm.post.call_count == 3
        # ...and they hit 3 distinct providers (max_attempts_per_provider=1).
        called_models = [c.kwargs["json"]["model"] for c in mocked.return_value.post.call_args_list]
        assert len(set(called_models)) == 3

    @pytest.mark.asyncio
    async def test_per_provider_ceiling_forces_distinct_providers(
        self, app_client, auth_headers, db_session, fixed_salt
    ):
        """max_attempts_per_provider=1 spreads attempts across providers."""
        import base64
        from models import Model, Channel, Setting
        from services.crypto import encrypt
        from unittest.mock import patch, AsyncMock, MagicMock

        db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
        db_session.commit()
        # Three providers, the first with TWO models. Without the per-provider
        # cap the loop would try groq/m0, groq/m1, sf/m0 (sorted by latency).
        # With per_provider=1 it must skip groq/m1 and spread: groq, sf, openrouter.
        ch0 = Channel(id="ch-pv0", provider_type="groq", name="p0",
                      api_key_enc=encrypt("sk-test-key", "test-admin-password", fixed_salt), enabled=True)
        ch1 = Channel(id="ch-pv1", provider_type="siliconflow", name="p1",
                      api_key_enc=encrypt("sk-test-key", "test-admin-password", fixed_salt), enabled=True)
        ch2 = Channel(id="ch-pv2", provider_type="openrouter", name="p2",
                      api_key_enc=encrypt("sk-test-key", "test-admin-password", fixed_salt), enabled=True)
        db_session.add_all([ch0, ch1, ch2])
        db_session.commit()
        m0a = Model(id="m-pv0a", channel_id=ch0.id, model_id="groq-model-a",
                    category="text", is_free=True, is_active=True, health_status="healthy",
                    last_response_ms=100)
        m0b = Model(id="m-pv0b", channel_id=ch0.id, model_id="groq-model-b",
                    category="text", is_free=True, is_active=True, health_status="healthy",
                    last_response_ms=110)
        m1a = Model(id="m-pv1a", channel_id=ch1.id, model_id="sf-model-a",
                    category="text", is_free=True, is_active=True, health_status="healthy",
                    last_response_ms=120)
        m1b = Model(id="m-pv1b", channel_id=ch1.id, model_id="sf-model-b",
                    category="text", is_free=True, is_active=True, health_status="healthy",
                    last_response_ms=130)
        m2a = Model(id="m-pv2a", channel_id=ch2.id, model_id="or-model-a",
                    category="text", is_free=True, is_active=True, health_status="healthy",
                    last_response_ms=140)
        db_session.add_all([m0a, m0b, m1a, m1b, m2a])
        db_session.commit()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "x"}
        mock_resp.headers = {}
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_cm) as mocked:
            await app_client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto:text",
                    "messages": [{"role": "user", "content": "hi"}],
                    "routing_policy": {"profile": "hotspot-classifier"},
                },
                headers=auth_headers,
            )
        called = [c.kwargs["json"]["model"] for c in mocked.return_value.post.call_args_list]
        # 3 attempts, and the two groq models were NOT both tried.
        assert len(called) == 3
        groq_tried = [m for m in called if m.startswith("groq")]
        assert len(groq_tried) == 1

    @pytest.mark.asyncio
    async def test_first_provider_500_switches_to_second_provider(
        self, app_client, auth_headers, db_session, fixed_salt
    ):
        """When the first provider fails, AC falls back to another provider."""
        import base64
        from models import Model, Channel, Setting
        from services.crypto import encrypt
        from unittest.mock import patch, AsyncMock, MagicMock

        db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
        db_session.commit()
        ch0 = Channel(id="ch-sw0", provider_type="groq", name="p0",
                      api_key_enc=encrypt("sk-test-key", "test-admin-password", fixed_salt), enabled=True)
        ch1 = Channel(id="ch-sw1", provider_type="siliconflow", name="p1",
                      api_key_enc=encrypt("sk-test-key", "test-admin-password", fixed_salt), enabled=True)
        db_session.add_all([ch0, ch1])
        db_session.commit()
        m0 = Model(id="m-sw0", channel_id=ch0.id, model_id="groq-model",
                   category="text", is_free=True, is_active=True, health_status="healthy",
                   last_response_ms=100)
        m1 = Model(id="m-sw1", channel_id=ch1.id, model_id="sf-model",
                   category="text", is_free=True, is_active=True, health_status="healthy",
                   last_response_ms=200)
        db_session.add_all([m0, m1])
        db_session.commit()

        # First call (groq) returns 500; second call (siliconflow) returns 200.
        fail_resp = MagicMock(status_code=500, headers={})
        fail_resp.json.return_value = {"error": "x"}
        ok_resp = MagicMock(status_code=200, headers={})
        ok_resp.json.return_value = {"id": "y", "choices": [{"message": {"content": "ok"}}]}
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(side_effect=[fail_resp, ok_resp])
        with patch("httpx.AsyncClient", return_value=mock_cm):
            resp = await app_client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto:text",
                    "messages": [{"role": "user", "content": "hi"}],
                    "routing_policy": {"profile": "hotspot-classifier"},
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        # The X-AC-Attempted-Models header records the cross-provider sequence.
        attempted = resp.headers["X-AC-Attempted-Models"].split(",")
        assert "groq-model" in attempted
        assert "sf-model" in attempted
