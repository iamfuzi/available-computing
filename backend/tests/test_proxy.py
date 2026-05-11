import pytest
import json
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
    async def test_requires_auth(self, app_client):
        resp = await app_client.get("/v1/models")
        assert resp.status_code == 403


class TestHealthAwareRouting:
    def test_down_model_skipped(self, db_session, sample_model, sample_channel):
        from api.proxy import _resolve_model
        sample_model.health_status = "down"
        db_session.add(sample_model)
        db_session.commit()
        model, _, _, _ = _resolve_model("test-model-free", db_session)
        assert model is None

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
