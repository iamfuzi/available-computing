import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from adapters.openrouter import OpenRouterAdapter
from adapters.base import ModelInfo


@pytest.fixture
def adapter():
    return OpenRouterAdapter()


def _make(raw_pricing):
    """Build a ModelInfo carrying only the given pricing dict in raw."""
    return ModelInfo(
        model_id="m",
        display_name="m",
        category="text",
        context_length=1000,
        raw=raw_pricing,
    )


class TestDetectFreeFromApi:
    """OpenRouter free detection keys off pricing.prompt / pricing.completion
    both being zero. The API has been observed returning these values as
    strings ("0"), ints (0), and floats (0.0); all must be recognised as
    free, while non-zero / malformed values must not raise."""

    @pytest.mark.parametrize("value", ["0", 0, 0.0, "0.0"])
    def test_free_when_both_prices_zero(self, adapter, value):
        m = _make({"pricing": {"prompt": value, "completion": value}})
        assert adapter.detect_free_from_api(m) == {
            "is_free": True,
            "free_type": "permanent",
        }

    @pytest.mark.parametrize(
        "prompt, completion",
        [
            ("0.0000001", "0.0000002"),  # tiny but non-zero
            ("1", "0"),
            ("0", "1"),
            (0.0000001, 0),
        ],
    )
    def test_paid_when_any_price_nonzero(self, adapter, prompt, completion):
        m = _make({"pricing": {"prompt": prompt, "completion": completion}})
        assert adapter.detect_free_from_api(m) == {"is_free": False}

    @pytest.mark.parametrize("raw", [{"pricing": {}}, {}])
    def test_paid_when_pricing_missing(self, adapter, raw):
        # Missing pricing fields default to 1 (treated as paid, not free).
        m = _make(raw)
        assert adapter.detect_free_from_api(m) == {"is_free": False}

    @pytest.mark.parametrize("value", ["free!", None, [0], {"x": 0}])
    def test_none_when_pricing_malformed(self, adapter, value):
        # Non-numeric values can't be parsed → defer to the whitelist
        # (None) rather than guessing.
        m = _make({"pricing": {"prompt": value, "completion": value}})
        assert adapter.detect_free_from_api(m) is None


# ── health_check probe tests ───────────────────────────────────────────────
#
# These cover every branch of OpenRouterAdapter.health_check by mocking the
# httpx.AsyncClient it builds internally. Real httpx.Response objects are
# used as return values so that the rate-limit-header parsers (which call
# response.headers.items()) keep working unchanged.

_BASE = "https://openrouter.ai/api/v1"


def _mock_client_returning(response):
    """Patch httpx.AsyncClient so its ``post`` resolves to ``response``.

    Returns the MagicMock stand-in for the async-client context manager, so
    callers can additionally assert on how it was used (URL/headers/payload).
    """
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_cm.post = AsyncMock(return_value=response)
    return mock_cm


def _mock_client_raising(exc):
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
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "healthy"
        assert info.error_code is None
        assert info.response_ms >= 0

    @pytest.mark.asyncio
    async def test_200_slow_when_over_threshold(self, adapter, monkeypatch):
        # Slow vs healthy is decided by SLOW_RESPONSE_THRESHOLD_MS. Lower it
        # so the (fast) mocked call still crosses the threshold.
        import adapters.openrouter as or_mod
        monkeypatch.setattr(or_mod, "SLOW_RESPONSE_THRESHOLD_MS", 0)
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I am a model"}}]},
        )
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code is None

    @pytest.mark.asyncio
    async def test_200_empty_content_is_down(self, adapter):
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "   "}}]},
        )
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "empty_response"

    @pytest.mark.asyncio
    async def test_200_missing_choices_is_down(self, adapter):
        # Malformed 200 body (no choices/message/content) → empty_response.
        resp = httpx.Response(200, json={"unexpected": "shape"})
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "empty_response"

    @pytest.mark.asyncio
    async def test_429_is_slow_not_down(self, adapter):
        resp = httpx.Response(429, json={"error": "rate limited"})
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "rate_limited"

    @pytest.mark.asyncio
    async def test_429_records_observed_rate_limits(self, adapter):
        # 429 responses from OpenRouter carry x-ratelimit-* headers; the
        # probe should surface them so the pool can rebalance.
        resp = httpx.Response(
            429,
            json={"error": "rate limited"},
            headers={
                "x-ratelimit-limit-requests": "30",
                "x-ratelimit-remaining-requests": "0",
            },
        )
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "rate_limited"
        assert info.observed_rate_limit == {"rpm": 30}
        assert info.observed_remaining == {"rpm_remaining": 0}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [401, 403])
    async def test_auth_errors_are_down(self, adapter, code):
        resp = httpx.Response(code, json={"error": {"message": "invalid key"}})
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "auth_failed"

    @pytest.mark.asyncio
    async def test_404_is_down(self, adapter):
        resp = httpx.Response(404, json={"error": "model not found"})
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "down"
        assert info.error_code == "not_found"

    @pytest.mark.asyncio
    async def test_500_is_slow_not_down(self, adapter):
        # 5xx is typically a transient upstream issue (OpenRouter is an
        # aggregator). A single 5xx must not eject the model from the pool —
        # mark it slow so it stays available at lower priority.
        resp = httpx.Response(500, json={"error": "internal"})
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_returning(resp)):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "server_error"

    @pytest.mark.asyncio
    async def test_timeout_is_slow_not_down(self, adapter):
        # A timeout on a free/queued model is transient and shouldn't drop it
        # out of the pool.
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_raising(httpx.TimeoutException("timed out"))):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "timeout"
        assert info.response_ms > 0

    @pytest.mark.asyncio
    async def test_network_error_is_slow_not_down(self, adapter):
        # Network blips are transient; keep the model in the pool rather than
        # marking it down.
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=_mock_client_raising(httpx.RequestError("connection refused"))):
            info = await adapter.health_check("m", "sk-test", _BASE)
        assert info.status == "slow"
        assert info.error_code == "network_error"

    @pytest.mark.asyncio
    async def test_probe_sends_referer_and_bearer(self, adapter):
        # OpenRouter requires the HTTP-Referer header to attribute traffic;
        # the probe must send it along with the bearer token.
        resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )
        cm = _mock_client_returning(resp)
        with patch("adapters.openrouter.httpx.AsyncClient", return_value=cm):
            await adapter.health_check("m", "sk-test", _BASE)
        cm.post.assert_awaited_once()
        _, kwargs = cm.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
        assert "HTTP-Referer" in kwargs["headers"]
        assert kwargs["json"]["model"] == "m"
