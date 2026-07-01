import pytest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, AsyncMock
from database import get_session


# ── Unit tests ────────────────────────────────────────────────────────────

class TestBuildOpenaiPayload:
    def test_basic_payload(self):
        from api.proxy import _build_openai_payload, ChatRequest, ChatMessage
        body = ChatRequest(model="gpt-4", messages=[ChatMessage(role="user", content="hi")])
        payload = _build_openai_payload(body)
        assert payload["model"] == "gpt-4"
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        assert payload["stream"] is False

    def test_optional_fields(self):
        from api.proxy import _build_openai_payload, ChatRequest, ChatMessage
        body = ChatRequest(
            model="gpt-4",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=100,
            temperature=0.7,
            stream=True,
        )
        payload = _build_openai_payload(body)
        assert payload["max_tokens"] == 100
        assert payload["temperature"] == 0.7
        assert payload["stream"] is True

    def test_optional_fields_omitted_when_none(self):
        from api.proxy import _build_openai_payload, ChatRequest, ChatMessage
        body = ChatRequest(model="gpt-4", messages=[ChatMessage(role="user", content="hi")])
        payload = _build_openai_payload(body)
        assert "max_tokens" not in payload
        assert "temperature" not in payload


class TestResolveModel:
    def test_finds_active_free_model(self, db_session, sample_model, sample_channel):
        from api.proxy import _resolve_model
        model, channel, adapter, key = _resolve_model("test-model-free", db_session)
        assert model is not None
        assert model.id == sample_model.id
        assert key == "sk-test-api-key-12345"

    def test_model_not_found(self, db_session):
        from api.proxy import _resolve_model
        model, channel, adapter, key = _resolve_model("nonexistent", db_session)
        assert model is None

    def test_inactive_model_skipped(self, db_session, sample_model, sample_channel):
        from api.proxy import _resolve_model
        sample_model.is_active = False
        db_session.add(sample_model)
        db_session.commit()
        model, _, _, _ = _resolve_model("test-model-free", db_session)
        assert model is None

    def test_not_free_model_skipped(self, db_session, sample_channel):
        from api.proxy import _resolve_model
        from models import Model
        paid = Model(
            id="mdl-paid",
            channel_id=sample_channel.id,
            model_id="paid-model",
            is_free=False,
            is_active=True,
        )
        db_session.add(paid)
        db_session.commit()
        model, _, _, _ = _resolve_model("paid-model", db_session)
        assert model is None

    def test_disabled_channel_skipped(self, db_session, sample_model, sample_channel):
        from api.proxy import _resolve_model
        sample_channel.enabled = False
        db_session.add(sample_channel)
        db_session.commit()
        model, _, _, _ = _resolve_model("test-model-free", db_session)
        assert model is None


class TestRateLimit:
    def test_rate_limit_429(self):
        from api.proxy import _check_proxy_rate_limit, _proxy_requests
        ip = "1.2.3.4"
        _proxy_requests.clear()
        with pytest.raises(Exception) as exc_info:
            for _ in range(61):
                _check_proxy_rate_limit(ip)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_local_rate_limit_is_scoped_to_api_key(self, app_client, db_session, monkeypatch):
        from models import ApiKey
        import hashlib
        import api.proxy as proxy

        monkeypatch.setattr(proxy, "PROXY_API_KEY_RATE_LIMIT", 1)
        monkeypatch.setattr(proxy, "PROXY_IP_FALLBACK_RATE_LIMIT", 100)

        raw1 = "ac_rate_limit_key_one"
        raw2 = "ac_rate_limit_key_two"
        for raw in (raw1, raw2):
            db_session.add(ApiKey(
                name=raw,
                key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                key_prefix=raw[:8],
                key_encrypted="",
                is_active=True,
            ))
        db_session.commit()

        payload = {"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]}

        first = await app_client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {raw1}"},
        )
        assert first.status_code == 404

        limited = await app_client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {raw1}"},
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "local_rate_limited"
        assert limited.headers["X-AC-Retry-After"] == "60"

        other_key = await app_client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {raw2}"},
        )
        assert other_key.status_code == 404


# ── Integration tests ────────────────────────────────────────────────────

class TestChatCompletionsAuth:
    @pytest.mark.asyncio
    async def test_unauthorized_no_token(self, app_client):
        resp = await app_client.post("/v1/chat/completions", json={
            "model": "test-model-free",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthorized_invalid_token(self, app_client):
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "test-model-free", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401


class TestChatCompletionsModelNotFound:
    @pytest.mark.asyncio
    async def test_model_not_found(self, app_client, auth_headers):
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["type"] == "invalid_request_error"

    @pytest.mark.asyncio
    async def test_model_not_free(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model
        paid = Model(
            id="mdl-paid2",
            channel_id=sample_channel.id,
            model_id="paid-model-2",
            is_free=False,
            is_active=True,
        )
        db_session.add(paid)
        db_session.commit()
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "paid-model-2", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestChatCompletionsNonStream:
    @pytest.mark.asyncio
    async def test_success(self, app_client, auth_headers, sample_model, sample_channel):
        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            }

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "test-model-free", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_upstream_500(self, app_client, auth_headers, sample_model, sample_channel):
        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.json.return_value = {"error": "internal"}

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "test-model-free", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
            assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_upstream_429(self, app_client, auth_headers, sample_model, sample_channel):
        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.json.return_value = {"error": "rate limited"}

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "test-model-free", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
            assert resp.status_code == 429


class TestChatCompletionsGemini:
    def _setup_gemini(self, db_session, sample_channel):
        from models import Model
        sample_channel.provider_type = "gemini"
        db_session.add(sample_channel)
        model = Model(
            id="mdl-gemini",
            channel_id=sample_channel.id,
            model_id="gemini-2.5-flash",
            is_free=True,
            is_active=True,
            health_status="healthy",
        )
        db_session.add(model)
        db_session.commit()
        return model

    @pytest.mark.asyncio
    async def test_gemini_success(self, app_client, auth_headers, db_session, sample_channel):
        self._setup_gemini(db_session, sample_channel)

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}],
            }

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["choices"][0]["message"]["content"] == "Hello from Gemini"

    @pytest.mark.asyncio
    async def test_gemini_stream_not_rejected(self, app_client, auth_headers, db_session, sample_channel):
        """Gemini streaming should no longer return 400 — it's now supported."""
        self._setup_gemini(db_session, sample_channel)
        resp = await app_client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-2.5-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            headers=auth_headers,
        )
        # Should not be 400 (old behavior was to reject streaming)
        assert resp.status_code != 400

    @pytest.mark.asyncio
    async def test_gemini_system_instruction(self, app_client, auth_headers, db_session, sample_channel):
        self._setup_gemini(db_session, sample_channel)

        captured_payload = {}

        async def fake_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            }
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = fake_post
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-2.5-flash",
                    "messages": [
                        {"role": "system", "content": "You are helpful"},
                        {"role": "user", "content": "hi"},
                    ],
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert captured_payload.get("systemInstruction", {}).get("parts", [{}])[0].get("text") == "You are helpful"


class TestOpenAIModelsList:
    @pytest.mark.asyncio
    async def test_returns_openai_format(self, app_client, auth_headers, sample_model, sample_channel):
        resp = await app_client.get("/v1/models", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        m = body["data"][0]
        assert m["id"] == "test-model-free"
        assert m["object"] == "model"
        assert "owned_by" in m

    @pytest.mark.asyncio
    async def test_excludes_inactive_models(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model
        inactive = Model(
            id="mdl-inactive",
            channel_id=sample_channel.id,
            model_id="inactive-model",
            is_free=True,
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()
        resp = await app_client.get("/v1/models", headers=auth_headers)
        body = resp.json()
        ids = [m["id"] for m in body["data"]]
        assert "inactive-model" not in ids

    @pytest.mark.asyncio
    async def test_excludes_paid_models(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model
        paid = Model(
            id="mdl-paid3",
            channel_id=sample_channel.id,
            model_id="paid-model-3",
            is_free=False,
            is_active=True,
        )
        db_session.add(paid)
        db_session.commit()
        resp = await app_client.get("/v1/models", headers=auth_headers)
        body = resp.json()
        ids = [m["id"] for m in body["data"]]
        assert "paid-model-3" not in ids

    @pytest.mark.asyncio
    async def test_excludes_rate_limited_models(self, app_client, auth_headers, sample_model, db_session):
        sample_model.health_status = "rate_limited"
        sample_model.rate_limited_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        db_session.add(sample_model)
        db_session.commit()
        resp = await app_client.get("/v1/models", headers=auth_headers)
        body = resp.json()
        ids = [m["id"] for m in body["data"]]
        assert sample_model.model_id not in ids

    @pytest.mark.asyncio
    async def test_requires_auth(self, app_client):
        resp = await app_client.get("/v1/models")
        assert resp.status_code == 403


class TestAcDiagnostics:
    @pytest.mark.asyncio
    async def test_ac_status_summarizes_routes(self, app_client, auth_headers, sample_model, db_session):
        sample_model.category = "text"
        sample_model.health_status = "healthy"
        db_session.add(sample_model)
        db_session.commit()

        resp = await app_client.get("/v1/ac/status", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "available_computing.status"
        assert body["available_model_count"] >= 1
        assert body["routes"]["auto:text"]["available"] is True
        assert body["routes"]["auto:text"]["selected_model"] == sample_model.model_id

    @pytest.mark.asyncio
    async def test_ac_models_exposes_route_eligibility_and_cooldown(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model

        db_session.add(Model(
            id="mdl-diag-ok",
            channel_id=sample_channel.id,
            model_id="diag-ok",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
        ))
        db_session.add(Model(
            id="mdl-diag-limited",
            channel_id=sample_channel.id,
            model_id="diag-limited",
            category="text",
            is_free=True,
            is_active=True,
            health_status="rate_limited",
            rate_limited_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
        db_session.commit()

        resp = await app_client.get("/v1/ac/models", headers=auth_headers)
        assert resp.status_code == 200
        by_id = {m["id"]: m for m in resp.json()["data"]}
        assert by_id["diag-ok"]["route_eligible"] is True
        assert by_id["diag-limited"]["route_eligible"] is False
        assert by_id["diag-limited"]["health_status"] == "rate_limited"
        assert by_id["diag-limited"]["rate_limited_until"] is not None

    @pytest.mark.asyncio
    async def test_ac_models_can_filter_available_only(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model

        db_session.add(Model(
            id="mdl-diag-filter-ok",
            channel_id=sample_channel.id,
            model_id="diag-filter-ok",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
        ))
        db_session.add(Model(
            id="mdl-diag-filter-slow",
            channel_id=sample_channel.id,
            model_id="diag-filter-slow",
            category="text",
            is_free=True,
            is_active=True,
            health_status="slow",
        ))
        db_session.commit()

        resp = await app_client.get("/v1/ac/models?include_unavailable=false", headers=auth_headers)
        assert resp.status_code == 200
        ids = {m["id"] for m in resp.json()["data"]}
        assert "diag-filter-ok" in ids
        assert "diag-filter-slow" not in ids

    @pytest.mark.asyncio
    async def test_self_test_reports_selected_candidate(self, app_client, auth_headers, sample_model, db_session):
        sample_model.category = "text"
        sample_model.health_status = "healthy"
        db_session.add(sample_model)
        db_session.commit()

        resp = await app_client.post("/v1/ac/self-test", json={"model": "auto:text"}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["selected_model"] == sample_model.model_id
        assert body["candidate_count"] >= 1

    @pytest.mark.asyncio
    async def test_self_test_reports_local_budget_block(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model, HealthRecord

        model = Model(
            id="mdl-self-budget",
            channel_id=sample_channel.id,
            model_id="self-budget",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            rate_limit='{"rpm": 1}',
        )
        db_session.add(model)
        db_session.commit()
        db_session.add(HealthRecord(
            model_id=model.id,
            status="healthy",
            is_passive=True,
            checked_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        resp = await app_client.post("/v1/ac/self-test", json={"model": "auto:text"}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["checked"][0]["reason"] == "local_rpm_exceeded"


class TestHealthAwareRouting:
    def test_down_model_skipped(self, db_session, sample_model, sample_channel):
        from api.proxy import _resolve_model
        sample_model.health_status = "down"
        db_session.add(sample_model)
        db_session.commit()
        model, _, _, _ = _resolve_model("test-model-free", db_session)
        assert model is None

    def test_success_rate_preferred_over_lower_latency(self, db_session, sample_channel):
        from models import Model, HealthRecord
        from api.proxy import _resolve_fast_model

        flaky = Model(
            id="mdl-flaky-fast",
            channel_id=sample_channel.id,
            model_id="flaky-fast",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=50,
        )
        stable = Model(
            id="mdl-stable-slower",
            channel_id=sample_channel.id,
            model_id="stable-slower",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=300,
        )
        db_session.add(flaky)
        db_session.add(stable)
        db_session.commit()
        for idx in range(4):
            db_session.add(HealthRecord(
                model_id=flaky.id,
                status="down" if idx < 3 else "healthy",
                is_passive=True,
                checked_at=datetime.now(timezone.utc) - timedelta(seconds=idx),
            ))
            db_session.add(HealthRecord(
                model_id=stable.id,
                status="healthy",
                is_passive=True,
                checked_at=datetime.now(timezone.utc) - timedelta(seconds=idx),
            ))
        db_session.commit()

        model, _, _, _ = _resolve_fast_model(db_session)
        assert model is not None
        assert model.model_id == "stable-slower"

    def test_healthy_preferred_over_slow(self, db_session, sample_channel):
        from models import Model
        from api.proxy import _resolve_model
        slow = Model(
            id="mdl-slow",
            channel_id=sample_channel.id,
            model_id="shared-model",
            is_free=True,
            is_active=True,
            health_status="slow",
            last_response_ms=3000,
        )
        fast = Model(
            id="mdl-fast",
            channel_id=sample_channel.id,
            model_id="shared-model",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=200,
        )
        db_session.add(slow)
        db_session.add(fast)
        db_session.commit()
        model, _, _, _ = _resolve_model("shared-model", db_session)
        assert model is not None
        assert model.health_status == "healthy"
        assert model.last_response_ms == 200

    def test_rate_limited_model_skipped(self, db_session, sample_model, sample_channel):
        from api.proxy import _resolve_model
        sample_model.health_status = "rate_limited"
        sample_model.rate_limited_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        db_session.add(sample_model)
        db_session.commit()
        model, _, _, _ = _resolve_model("test-model-free", db_session)
        assert model is None


class TestAutoRouting:
    @pytest.mark.asyncio
    async def test_auto_text_resolves(self, app_client, auth_headers, sample_model, sample_channel):
        from models import Model
        sample_model.category = "text"
        sample_model.health_status = "healthy"
        db_session = app_client._transport.app.dependency_overrides[get_session]()
        db_session.add(sample_model)
        db_session.commit()

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            }

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_auto_text_falls_back_after_429(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model

        first = Model(
            id="mdl-rate-first",
            channel_id=sample_channel.id,
            model_id="first-free",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
        )
        second = Model(
            id="mdl-rate-second",
            channel_id=sample_channel.id,
            model_id="second-free",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=200,
        )
        db_session.add(first)
        db_session.add(second)
        db_session.commit()

        posted_models = []

        async def fake_post(url, **kwargs):
            posted_models.append(kwargs["json"]["model"])
            mock_resp = MagicMock()
            if len(posted_models) == 1:
                mock_resp.status_code = 429
                mock_resp.headers = {"Retry-After": "30"}
                return mock_resp
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.json.return_value = {
                "id": "chatcmpl-456",
                "choices": [{"message": {"role": "assistant", "content": "fallback ok"}}],
            }
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = fake_post
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert posted_models == ["first-free", "second-free"]
        assert resp.headers["X-AC-Route"] == "auto:text"
        assert resp.headers["X-AC-Selected-Model"] == "second-free"
        assert resp.headers["X-AC-Attempted-Models"] == "first-free,second-free"
        assert resp.headers["X-AC-Fallback-Count"] == "1"
        db_session.refresh(first)
        assert first.health_status == "rate_limited"
        assert first.rate_limited_until is not None

    @pytest.mark.asyncio
    async def test_auto_text_all_rate_limited_has_structured_error(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model

        db_session.add(Model(
            id="mdl-only-rate",
            channel_id=sample_channel.id,
            model_id="only-free",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
        ))
        db_session.commit()

        async def fake_post(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.headers = {"Retry-After": "45"}
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = fake_post
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )

        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "all_candidates_rate_limited"
        assert body["error"]["retry_after"] == 45
        assert resp.headers["X-AC-Retry-After"] == "45"
        assert resp.headers["X-AC-Attempted-Models"] == "only-free"

    @pytest.mark.asyncio
    async def test_auto_text_skips_busy_model(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model
        from api.proxy import _model_semaphores, _model_slot_key
        import asyncio

        first = Model(
            id="mdl-busy-first",
            channel_id=sample_channel.id,
            model_id="busy-first",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
        )
        second = Model(
            id="mdl-busy-second",
            channel_id=sample_channel.id,
            model_id="busy-second",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=200,
        )
        db_session.add(first)
        db_session.add(second)
        db_session.commit()

        sem = asyncio.Semaphore(1)
        await sem.acquire()
        _model_semaphores[_model_slot_key(sample_channel, first)] = sem

        posted_models = []

        async def fake_post(url, **kwargs):
            posted_models.append(kwargs["json"]["model"])
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.json.return_value = {
                "id": "chatcmpl-busy",
                "choices": [{"message": {"role": "assistant", "content": "busy fallback ok"}}],
            }
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = fake_post
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert posted_models == ["busy-second"]
        assert resp.headers["X-AC-Selected-Model"] == "busy-second"
        assert resp.headers["X-AC-Attempted-Models"] == "busy-second"

    @pytest.mark.asyncio
    async def test_auto_text_all_busy_has_structured_error(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model
        from api.proxy import _model_semaphores, _model_slot_key
        import asyncio

        only = Model(
            id="mdl-busy-only",
            channel_id=sample_channel.id,
            model_id="busy-only",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
        )
        db_session.add(only)
        db_session.commit()

        sem = asyncio.Semaphore(1)
        await sem.acquire()
        _model_semaphores[_model_slot_key(sample_channel, only)] = sem

        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )

        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "all_candidates_busy"
        assert body["error"]["attempted_models"] == ["busy-only"]
        assert resp.headers["X-AC-Attempted-Models"] == "busy-only"

    @pytest.mark.asyncio
    async def test_auto_text_skips_local_rpm_budget(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model, HealthRecord

        first = Model(
            id="mdl-budget-first",
            channel_id=sample_channel.id,
            model_id="budget-first",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
            rate_limit='{"rpm": 1}',
        )
        second = Model(
            id="mdl-budget-second",
            channel_id=sample_channel.id,
            model_id="budget-second",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=200,
        )
        db_session.add(first)
        db_session.add(second)
        db_session.commit()
        db_session.add(HealthRecord(
            model_id=first.id,
            status="healthy",
            is_passive=True,
            checked_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        posted_models = []

        async def fake_post(url, **kwargs):
            posted_models.append(kwargs["json"]["model"])
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.json.return_value = {
                "id": "chatcmpl-budget",
                "choices": [{"message": {"role": "assistant", "content": "budget fallback ok"}}],
            }
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = fake_post
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert posted_models == ["budget-second"]
        assert resp.headers["X-AC-Selected-Model"] == "budget-second"

    @pytest.mark.asyncio
    async def test_auto_text_all_local_budget_limited(self, app_client, auth_headers, db_session, sample_channel):
        from models import Model, HealthRecord

        only = Model(
            id="mdl-budget-only",
            channel_id=sample_channel.id,
            model_id="budget-only",
            category="text",
            is_free=True,
            is_active=True,
            health_status="healthy",
            last_response_ms=100,
            rate_limit='{"rpm": 1}',
        )
        db_session.add(only)
        db_session.commit()
        db_session.add(HealthRecord(
            model_id=only.id,
            status="healthy",
            is_passive=True,
            checked_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )

        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "local_model_budget_exceeded"
        assert resp.headers["X-AC-Attempted-Models"] == "budget-only"

    @pytest.mark.asyncio
    async def test_auto_unknown_category_404(self, app_client, auth_headers):
        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "auto:nonexistent", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_auto_no_available_models_404(self, app_client, auth_headers, db_session, sample_channel):
        """All models down → auto:text returns 404."""
        from models import Model
        down = Model(
            id="mdl-down2",
            channel_id=sample_channel.id,
            model_id="down-text-model",
            category="text",
            is_free=True,
            is_active=True,
            health_status="down",
        )
        db_session.add(down)
        db_session.commit()

        resp = await app_client.post(
            "/v1/chat/completions",
            json={"model": "auto:text", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestSmartFastRouting:
    """auto:smart picks the largest model; auto:fast picks the fastest.
    Generic routes prefer text chat models; explicit auto:<category> routes
    select other categories."""

    def _make_model(self, channel_id, mid, param_size=None, health="healthy", ms=None):
        from models import Model
        return Model(
            channel_id=channel_id,
            model_id=mid,
            category="text",
            is_free=True,
            is_active=True,
            health_status=health,
            last_response_ms=ms,
            param_size=param_size,
        )

    def test_smart_picks_largest_param_size(self, db_session, sample_channel):
        from api.proxy import _resolve_smart_model
        db_session.add(self._make_model(sample_channel.id, "qwen-7b", param_size=7, ms=100))
        db_session.add(self._make_model(sample_channel.id, "qwen-72b", param_size=72, ms=900))
        db_session.add(self._make_model(sample_channel.id, "qwen-32b", param_size=32, ms=200))
        db_session.commit()
        model, _, _, _ = _resolve_smart_model(db_session)
        assert model.model_id == "qwen-72b"

    def test_smart_health_bucket_beats_size(self, db_session, sample_channel):
        # A healthy 7b beats a slow 72b — health is the primary sort key.
        from api.proxy import _resolve_smart_model
        db_session.add(self._make_model(sample_channel.id, "qwen-72b", param_size=72, health="slow"))
        db_session.add(self._make_model(sample_channel.id, "qwen-7b", param_size=7, health="healthy"))
        db_session.commit()
        model, _, _, _ = _resolve_smart_model(db_session)
        assert model.model_id == "qwen-7b"

    def test_smart_unknown_size_sorts_last(self, db_session, sample_channel):
        from api.proxy import _resolve_smart_model
        db_session.add(self._make_model(sample_channel.id, "gpt-4o", param_size=None))
        db_session.add(self._make_model(sample_channel.id, "qwen-7b", param_size=7))
        db_session.commit()
        model, _, _, _ = _resolve_smart_model(db_session)
        assert model.model_id == "qwen-7b"

    def test_smart_no_candidates_returns_none(self, db_session, sample_channel):
        from api.proxy import _resolve_smart_model
        model, channel, adapter, key = _resolve_smart_model(db_session)
        assert model is None

    def test_fast_picks_lowest_latency(self, db_session, sample_channel):
        from api.proxy import _resolve_fast_model
        db_session.add(self._make_model(sample_channel.id, "big-slow", param_size=72, ms=2000))
        db_session.add(self._make_model(sample_channel.id, "small-fast", param_size=7, ms=100))
        db_session.commit()
        model, _, _, _ = _resolve_fast_model(db_session)
        assert model.model_id == "small-fast"

    def test_smart_prefers_text_category(self, db_session, sample_channel):
        # Generic chat routing should not pick a code/vision model for ordinary
        # text requests, even if that model is larger.
        from api.proxy import _resolve_smart_model
        from models import Model
        db_session.add(self._make_model(sample_channel.id, "text-7b", param_size=7))
        code = self._make_model(sample_channel.id, "code-32b", param_size=32)
        code.category = "code"
        db_session.add(code)
        db_session.commit()
        model, _, _, _ = _resolve_smart_model(db_session)
        assert model.model_id == "text-7b"

    def test_fast_prefers_text_category(self, db_session, sample_channel):
        from api.proxy import _resolve_fast_model
        db_session.add(self._make_model(sample_channel.id, "text-slower", ms=300))
        vision = self._make_model(sample_channel.id, "vision-fast", ms=10)
        vision.category = "vision"
        db_session.add(vision)
        db_session.commit()
        model, _, _, _ = _resolve_fast_model(db_session)
        assert model.model_id == "text-slower"

    @pytest.mark.asyncio
    async def test_smart_http_endpoint(self, app_client, auth_headers, db_session, sample_channel):
        """auto:smart flows through the HTTP endpoint end-to-end."""
        from models import Model
        big = Model(
            channel_id=sample_channel.id, model_id="big-72b", category="text",
            is_free=True, is_active=True, health_status="healthy",
            last_response_ms=500, param_size=72,
        )
        small = Model(
            channel_id=sample_channel.id, model_id="small-7b", category="text",
            is_free=True, is_active=True, health_status="healthy",
            last_response_ms=100, param_size=7,
        )
        db_session.add(big)
        db_session.add(small)
        db_session.commit()

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            }
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/chat/completions",
                json={"model": "auto:smart", "messages": [{"role": "user", "content": "hi"}]},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        # The smart router must have selected the 72b model, not the faster 7b.
        sent_payload = mock_cm.post.call_args.kwargs["json"]
        assert sent_payload["model"] == "big-72b"


class TestAdminModelSort:
    """The admin /api/v1/models list honours sort_by to mirror the
    auto:smart / auto:fast router choice in the UI."""

    def _seed(self, db_session, sample_channel):
        from models import Model
        db_session.add(Model(
            channel_id=sample_channel.id, model_id="small-7b", category="text",
            is_free=True, is_active=True, health_status="healthy",
            last_response_ms=100, param_size=7,
        ))
        db_session.add(Model(
            channel_id=sample_channel.id, model_id="big-72b", category="text",
            is_free=True, is_active=True, health_status="healthy",
            last_response_ms=500, param_size=72,
        ))
        db_session.add(Model(
            channel_id=sample_channel.id, model_id="closed-unknown", category="text",
            is_free=True, is_active=True, health_status="healthy",
            last_response_ms=300, param_size=None,
        ))
        db_session.commit()

    @pytest.mark.asyncio
    async def test_default_sort_is_latency(self, app_client, auth_headers, db_session, sample_channel):
        self._seed(db_session, sample_channel)
        resp = await app_client.get("/api/v1/models", headers=auth_headers)
        assert resp.status_code == 200
        ids = [m["model_id"] for m in resp.json()]
        # latency ascending: 100, 300, 500
        assert ids == ["small-7b", "closed-unknown", "big-72b"]

    @pytest.mark.asyncio
    async def test_smart_sort_is_param_size_desc(self, app_client, auth_headers, db_session, sample_channel):
        self._seed(db_session, sample_channel)
        resp = await app_client.get("/api/v1/models?sort_by=smart", headers=auth_headers)
        assert resp.status_code == 200
        ids = [m["model_id"] for m in resp.json()]
        # param_size descending: 72, 7, None(last)
        assert ids == ["big-72b", "small-7b", "closed-unknown"]

    @pytest.mark.asyncio
    async def test_response_includes_param_size(self, app_client, auth_headers, db_session, sample_channel):
        self._seed(db_session, sample_channel)
        resp = await app_client.get("/api/v1/models", headers=auth_headers)
        by_id = {m["model_id"]: m for m in resp.json()}
        assert by_id["big-72b"]["param_size"] == 72
        assert by_id["closed-unknown"]["param_size"] is None


class TestNonChatModels:
    """embedding / rerank models: queryable via /v1/models?category= and callable
    via /v1/embeddings and /v1/rerank."""

    def _sf_channel(self, db_session, fixed_salt):
        """A SiliconFlow channel (the only provider with free rerank/embedding)."""
        import base64
        from models import Channel, Setting
        from services.crypto import encrypt
        db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
        db_session.commit()
        ch = Channel(
            id="ch-sf-test", provider_type="siliconflow", name="SF",
            api_key_enc=encrypt("sk-test", "test-admin-password", fixed_salt),
            base_url="https://api.siliconflow.cn/v1", enabled=True,
        )
        db_session.add(ch)
        db_session.commit()
        return ch

    def _seed_nonchat(self, db_session, ch_id):
        from models import Model
        for mid, cat in [
            ("BAAI/bge-m3", "embedding"),
            ("netease-youdao/bce-embedding-base_v1", "embedding"),
            ("BAAI/bge-reranker-v2-m3", "rerank"),
            ("Qwen/Qwen3-Reranker-4B", "rerank"),
            ("meta-llama/llama-3.3-70b", "text"),
        ]:
            db_session.add(Model(
                channel_id=ch_id, model_id=mid, category=cat,
                is_free=True, is_active=True, health_status="healthy",
            ))
        db_session.commit()

    @pytest.mark.asyncio
    async def test_models_default_excludes_nonchat(self, app_client, auth_headers, db_session, fixed_salt):
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        ids = {m["id"] for m in (await self._list(app_client, auth_headers))}
        assert "meta-llama/llama-3.3-70b" in ids
        assert "BAAI/bge-m3" not in ids
        assert "BAAI/bge-reranker-v2-m3" not in ids

    @pytest.mark.asyncio
    async def test_models_category_embedding(self, app_client, auth_headers, db_session, fixed_salt):
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        ids = {m["id"] for m in (await self._list(app_client, auth_headers, "embedding"))}
        assert "BAAI/bge-m3" in ids
        assert "BAAI/bge-reranker-v2-m3" not in ids

    @pytest.mark.asyncio
    async def test_models_category_rerank(self, app_client, auth_headers, db_session, fixed_salt):
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        ids = {m["id"] for m in (await self._list(app_client, auth_headers, "rerank"))}
        assert "BAAI/bge-reranker-v2-m3" in ids
        assert "BAAI/bge-m3" not in ids

    @pytest.mark.asyncio
    async def test_models_category_all(self, app_client, auth_headers, db_session, fixed_salt):
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        ids = {m["id"] for m in (await self._list(app_client, auth_headers, "all"))}
        assert "BAAI/bge-m3" in ids
        assert "BAAI/bge-reranker-v2-m3" in ids
        assert "meta-llama/llama-3.3-70b" in ids

    async def _list(self, app_client, auth_headers, category=None):
        url = "/v1/models" + (f"?category={category}" if category else "")
        resp = await app_client.get(url, headers=auth_headers)
        assert resp.status_code == 200
        return resp.json()["data"]

    @pytest.mark.asyncio
    async def test_embeddings_routes_and_forwards(self, app_client, auth_headers, db_session, fixed_salt):
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/embeddings",
                json={"model": "BAAI/bge-m3", "input": "hello"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["embedding"] == [0.1, 0.2]
        # Forwarded to the upstream /embeddings with the model id + input
        sent = mock_cm.post.call_args
        assert sent.args[0].endswith("/embeddings")
        assert sent.kwargs["json"]["model"] == "BAAI/bge-m3"
        assert sent.kwargs["json"]["input"] == "hello"

    @pytest.mark.asyncio
    async def test_embeddings_404_when_no_model(self, app_client, auth_headers, db_session, fixed_salt):
        resp = await app_client.post(
            "/v1/embeddings",
            json={"model": "nonexistent-embed", "input": "hi"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_embeddings_does_not_match_chat_model(self, app_client, auth_headers, db_session, fixed_salt):
        # A chat model must not be routable via the embedding endpoint.
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        resp = await app_client.post(
            "/v1/embeddings",
            json={"model": "meta-llama/llama-3.3-70b", "input": "hi"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rerank_routes_and_forwards(self, app_client, auth_headers, db_session, fixed_salt):
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)
        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_cm.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_cm

            resp = await app_client.post(
                "/v1/rerank",
                json={"model": "BAAI/bge-reranker-v2-m3", "query": "hello",
                      "documents": ["hi", "world"]},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["relevance_score"] == 0.9
        sent = mock_cm.post.call_args
        assert sent.args[0].endswith("/rerank")
        assert sent.kwargs["json"]["query"] == "hello"
        assert sent.kwargs["json"]["documents"] == ["hi", "world"]

    @pytest.mark.asyncio
    async def test_rerank_404_when_no_model(self, app_client, auth_headers, db_session, fixed_salt):
        resp = await app_client.post(
            "/v1/rerank",
            json={"model": "nope", "query": "q", "documents": ["d"]},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_nonchat_endpoints_accept_apikey(self, app_client, db_session, fixed_salt):
        # API Key auth (not just JWT) must work for the new endpoints.
        from models import ApiKey
        import hashlib
        raw = "ac_testkey_nonchat_1234567890"
        db_session.add(ApiKey(
            name="t", key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:8], key_encrypted="", is_active=True,
        ))
        db_session.commit()
        ch = self._sf_channel(db_session, fixed_salt)
        self._seed_nonchat(db_session, ch.id)

        resp = await app_client.get(
            "/v1/models?category=embedding",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        assert any(m["id"] == "BAAI/bge-m3" for m in resp.json()["data"])
