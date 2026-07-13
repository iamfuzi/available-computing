import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from adapters.agnes import AgnesAdapter, _infer_category
from adapters.base import ModelInfo


@pytest.fixture
def adapter():
    return AgnesAdapter()


_BASE = "https://apihub.agnes-ai.com/v1"


# ── identity ────────────────────────────────────────────────────────────────


class TestIdentity:
    def test_provider_id(self, adapter):
        assert adapter.provider_id == "agnes"

    def test_display_name(self, adapter):
        assert adapter.display_name == "Agnes AI"

    def test_default_base_url(self, adapter):
        assert adapter.default_base_url == "https://apihub.agnes-ai.com/v1"


# ── category inference ──────────────────────────────────────────────────────


class TestInferCategory:
    """Generation models (image/video) must be classified away from text so
    the chat prober skips them and they stay out of /v1/models. The chat-capable
    flash model falls through to text."""

    @pytest.mark.parametrize("model_id", ["agnes-2.0-flash", "agnes-1.5-flash"])
    def test_chat_models_are_text(self, model_id):
        assert _infer_category(model_id) == "text"

    @pytest.mark.parametrize("model_id", ["agnes-image-2.0-flash", "agnes-image-2.1-flash"])
    def test_image_models_are_image(self, model_id):
        assert _infer_category(model_id) == "image"

    def test_video_models_are_video(self):
        assert _infer_category("agnes-video-v2.0") == "video"


# ── detect_free_from_api ────────────────────────────────────────────────────


class TestDetectFreeFromApi:
    """Agnes /models carries no pricing field; detection always defers to the
    whitelist rather than guessing."""

    def test_returns_none(self, adapter):
        m = ModelInfo(model_id="agnes-2.0-flash", display_name="x", category="text", raw={})
        assert adapter.detect_free_from_api(m) is None

    def test_returns_none_even_with_raw_fields(self, adapter):
        # An unsuspected field must not accidentally look like a pricing signal.
        m = ModelInfo(model_id="x", display_name="x", category="text", raw={"owned_by": "custom"})
        assert adapter.detect_free_from_api(m) is None


# ── list_models ─────────────────────────────────────────────────────────────


def _mock_get_client(response, url=f"{_BASE}/models"):
    # raise_for_status() requires a request attached to the response.
    response.request = httpx.Request("GET", url)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_cm.get = AsyncMock(return_value=response)
    return mock_cm


class TestListModels:
    @pytest.mark.asyncio
    async def test_parses_models_and_categories(self, adapter):
        resp = httpx.Response(200, json={
            "data": [
                {"id": "agnes-2.0-flash"},
                {"id": "agnes-image-2.0-flash"},
                {"id": "agnes-video-v2.0"},
            ]
        })
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_get_client(resp)):
            models = await adapter.list_models("sk-test", _BASE)
        ids = [m.model_id for m in models]
        assert ids == ["agnes-2.0-flash", "agnes-image-2.0-flash", "agnes-video-v2.0"]
        cats = {m.model_id: m.category for m in models}
        assert cats == {
            "agnes-2.0-flash": "text",
            "agnes-image-2.0-flash": "image",
            "agnes-video-v2.0": "video",
        }


# ── validate_key ────────────────────────────────────────────────────────────


class TestValidateKey:
    @pytest.mark.asyncio
    async def test_401_raises(self, adapter):
        resp = httpx.Response(401, json={"error": "invalid"})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_get_client(resp)):
            with pytest.raises(ValueError, match="Invalid API key"):
                await adapter.validate_key("bad", _BASE)

    @pytest.mark.asyncio
    async def test_200_passes(self, adapter):
        resp = httpx.Response(200, json={"data": []})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_get_client(resp)):
            await adapter.validate_key("good", _BASE)  # no raise

    @pytest.mark.asyncio
    async def test_500_raises_httpx_error(self, adapter):
        resp = httpx.Response(500)
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_get_client(resp)):
            with pytest.raises(httpx.HTTPStatusError):
                await adapter.validate_key("sk", _BASE)


# ── health_check probe tests ────────────────────────────────────────────────
#
# Cover every branch of AgnesAdapter.health_check by mocking the httpx client
# it builds internally. Real httpx.Response objects are returned so the
# rate-limit-header parsers keep working.

def _mock_post_client_returning(response):
    """Patch httpx.AsyncClient so its ``post`` resolves to ``response``."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_cm.post = AsyncMock(return_value=response)
    return mock_cm


def _mock_post_client_raising(exc):
    """Patch httpx.AsyncClient so its ``post`` raises ``exc``."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_cm.post = AsyncMock(side_effect=exc)
    return mock_cm


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_200_fast_is_healthy(self, adapter):
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I am a model"}}]},
        )
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "healthy"
        assert info.error_code is None
        assert info.response_ms >= 0

    @pytest.mark.asyncio
    async def test_200_slow_when_over_threshold(self, adapter, monkeypatch):
        import adapters.agnes as agnes_mod
        monkeypatch.setattr(agnes_mod, "SLOW_RESPONSE_THRESHOLD_MS", 0)
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I am a model"}}]},
        )
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code is None

    @pytest.mark.asyncio
    async def test_200_empty_content_is_down(self, adapter):
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "   "}}]},
        )
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "empty_response"

    @pytest.mark.asyncio
    async def test_200_missing_choices_is_down(self, adapter):
        resp = httpx.Response(200, json={"unexpected": "shape"})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "empty_response"

    @pytest.mark.asyncio
    async def test_429_is_slow_not_down(self, adapter):
        resp = httpx.Response(429, json={"error": "rate limited"})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "rate_limited"

    @pytest.mark.asyncio
    async def test_429_records_observed_rate_limits(self, adapter):
        # Agnes has been observed to return no standard rate-limit headers
        # (only LiteLLM-internal ones), so the parsers return None. The probe
        # must tolerate that and still mark the model slow, not crash.
        resp = httpx.Response(
            429,
            json={"error": "rate limited"},
            headers={"x-litellm-key-spend": "0.0"},
        )
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "rate_limited"
        assert info.observed_rate_limit is None
        assert info.observed_remaining is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [401, 403])
    async def test_auth_errors_are_down(self, adapter, code):
        resp = httpx.Response(code, json={"error": {"message": "invalid key"}})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "auth_failed"

    @pytest.mark.asyncio
    async def test_404_is_down(self, adapter):
        resp = httpx.Response(404, json={"error": "model not found"})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "not_found"

    @pytest.mark.asyncio
    async def test_500_is_slow(self, adapter):
        resp = httpx.Response(500, json={"error": "internal"})
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_returning(resp)):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "server_error"

    @pytest.mark.asyncio
    async def test_timeout_is_slow(self, adapter):
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_raising(httpx.TimeoutException("timed out"))):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "timeout"
        assert info.response_ms > 0

    @pytest.mark.asyncio
    async def test_network_error_is_slow(self, adapter):
        with patch("adapters.agnes.httpx.AsyncClient", return_value=_mock_post_client_raising(httpx.RequestError("connection refused"))):
            info = await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "network_error"

    @pytest.mark.asyncio
    async def test_probe_sends_bearer_and_target_model(self, adapter):
        # The probe must forward the bearer token and target the requested
        # model id, so the health result reflects the right model.
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )
        cm = _mock_post_client_returning(resp)
        with patch("adapters.agnes.httpx.AsyncClient", return_value=cm):
            await adapter.health_check("agnes-2.0-flash", "sk-test", _BASE)
        cm.post.assert_awaited_once()
        _, kwargs = cm.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
        assert kwargs["json"]["model"] == "agnes-2.0-flash"
