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


# ── text probe: reasoning models must not be misclassified ────────────────


@pytest.mark.asyncio
async def test_text_probe_reasoning_only_content_is_alive():
    # Thinking GLM models (4.7-flash / 4.5-flash) answer in reasoning_content
    # when the token budget is tight; empty content alone must not mean down.
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}]},
    )
    with patch("adapters.zhipu.httpx.AsyncClient", return_value=_client(response)):
        info = await ZhiPuAdapter().health_check("glm-4.7-flash", "sk-test", _BASE)
    assert info.status in {"healthy", "slow"}
    assert info.error_code is None


@pytest.mark.asyncio
async def test_text_probe_content_and_reasoning_both_empty_is_down():
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "", "reasoning_content": ""}}]},
    )
    with patch("adapters.zhipu.httpx.AsyncClient", return_value=_client(response)):
        info = await ZhiPuAdapter().health_check("glm-4.7-flash", "sk-test", _BASE)
    assert info.status == "down"
    assert info.error_code == "empty_response"


@pytest.mark.asyncio
async def test_text_probe_budget_leaves_room_for_reasoning():
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "我是GLM"}}]},
    )
    client = _client(response)
    with patch("adapters.zhipu.httpx.AsyncClient", return_value=client):
        await ZhiPuAdapter().health_check("glm-4.5-flash", "sk-test", _BASE)
    _, kwargs = client.post.call_args
    assert kwargs["json"]["max_tokens"] >= 200


@pytest.mark.asyncio
async def test_text_probe_multimodal_content_list_still_works():
    # content as a list of parts (vision models answering text) keeps working.
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": [{"type": "text", "text": "ok"}]}}]},
    )
    with patch("adapters.zhipu.httpx.AsyncClient", return_value=_client(response)):
        info = await ZhiPuAdapter().health_check("glm-4.6v-flash", "sk-test", _BASE)
    assert info.error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "expects_thinking_switch"),
    [
        ("glm-4.5-flash", True),
        ("glm-4.7-flash", True),
        ("glm-4.6v-flash", True),
        ("glm-z1-flash", False),   # older series: fast, keep legacy payload
        ("glm-4-flash", False),
    ],
)
async def test_thinking_switch_only_for_supported_series(model_id, expects_thinking_switch):
    # 4.5+ series probes disable thinking (28s → 11s latency); older ids keep
    # the legacy payload.
    response = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    client = _client(response)
    with patch("adapters.zhipu.httpx.AsyncClient", return_value=client):
        await ZhiPuAdapter().health_check(model_id, "sk-test", _BASE)
    _, kwargs = client.post.call_args
    has_switch = "thinking" in kwargs["json"]
    assert has_switch is expects_thinking_switch
