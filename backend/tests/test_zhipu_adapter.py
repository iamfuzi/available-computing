from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.zhipu import ZhiPuAdapter, _infer_category


_BASE = "https://open.bigmodel.cn/api/paas/v4"


def _client(response=None, *, error=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response, side_effect=error)
    return client


def test_infers_generation_categories():
    assert _infer_category("cogview-3-flash") == "image"
    assert _infer_category("cogvideox-flash") == "video"
    assert _infer_category("glm-4-flash") == "text"


@pytest.mark.asyncio
async def test_image_probe_uses_image_endpoint_and_validates_url():
    response = httpx.Response(
        200,
        json={"created": 1, "data": [{"url": "https://example.test/image.png"}]},
    )
    client = _client(response)
    with patch("adapters.zhipu.httpx.AsyncClient", return_value=client):
        info = await ZhiPuAdapter().health_check(
            "cogview-3-flash", "sk-test", _BASE
        )

    assert info.status in {"healthy", "slow"}
    assert info.error_code is None
    args, kwargs = client.post.call_args
    assert args[0] == f"{_BASE}/images/generations"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["json"] == {
        "model": "cogview-3-flash",
        "prompt": "白色背景上的一个蓝色圆点",
        "quality": "standard",
        "size": "1024x1024",
    }


@pytest.mark.asyncio
async def test_image_probe_rejects_empty_response():
    response = httpx.Response(200, json={"data": []})
    with patch(
        "adapters.zhipu.httpx.AsyncClient",
        return_value=_client(response),
    ):
        info = await ZhiPuAdapter().health_check(
            "cogview-3-flash", "sk-test", _BASE
        )
    assert info.status == "down"
    assert info.error_code == "empty_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error"),
    [
        (429, "slow", "rate_limited"),
        (401, "down", "auth_failed"),
        (403, "down", "auth_failed"),
        (404, "down", "not_found"),
        (500, "slow", "server_error"),
    ],
)
async def test_image_probe_maps_upstream_errors(
    status_code, expected_status, expected_error
):
    response = httpx.Response(status_code, json={"error": "failed"})
    with patch(
        "adapters.zhipu.httpx.AsyncClient",
        return_value=_client(response),
    ):
        info = await ZhiPuAdapter().health_check(
            "cogview-3-flash", "sk-test", _BASE
        )
    assert info.status == expected_status
    assert info.error_code == expected_error


@pytest.mark.asyncio
async def test_image_probe_timeout_is_slow():
    with patch(
        "adapters.zhipu.httpx.AsyncClient",
        return_value=_client(error=httpx.TimeoutException("timed out")),
    ):
        info = await ZhiPuAdapter().health_check(
            "cogview-3-flash", "sk-test", _BASE
        )
    assert info.status == "slow"
    assert info.error_code == "timeout"
    assert info.response_ms >= 60000
