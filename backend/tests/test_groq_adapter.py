from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters.groq import GroqAdapter, _infer_category


_BASE = "https://api.groq.com/openai/v1"


def _client(response=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    return client


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("openai/gpt-oss-120b", "text"),
        ("qwen/qwen3.8-27b", "text"),
        ("allam-2-7b", "text"),
        ("whisper-large-v3", "audio"),
        ("whisper-large-v3-turbo", "audio"),
        ("canopylabs/orpheus-v1-english", "audio"),
        ("canopylabs/orpheus-arabic-saudi", "audio"),
        ("meta-llama/llama-prompt-guard-2-86m", "guard"),
        ("openai/gpt-oss-safeguard-20b", "guard"),
    ],
)
def test_category_inference(model_id, expected):
    # Audio endpoints and safety classifiers must never land in the chat pool.
    assert _infer_category(model_id) == expected


def test_guard_category_is_not_chat_routable():
    from services.router.scoring import NON_CHAT_CATEGORIES
    assert "guard" in NON_CHAT_CATEGORIES


@pytest.mark.asyncio
async def test_probe_reasoning_only_content_is_alive():
    # gpt-oss with a tight budget answers only in `reasoning` (measured
    # 2026-09-30: mt=20 → content=''), which must not read as empty_response.
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "", "reasoning": "thinking"}}]},
        headers={"x-ratelimit-limit-requests": "1000", "x-ratelimit-limit-tokens": "8000"},
    )
    with patch("adapters.groq.httpx.AsyncClient", return_value=_client(response)):
        info = await GroqAdapter().health_check("openai/gpt-oss-20b", "gsk-test", _BASE)
    assert info.status in {"healthy", "slow"}
    assert info.error_code is None
    # Groq's limit-requests header is per-DAY — stored as rpd, never rpm=1000.
    assert info.observed_rate_limit == {"rpd": 1000, "tpm": 8000}


@pytest.mark.asyncio
async def test_probe_budget_leaves_room_for_reasoning():
    response = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    client = _client(response)
    with patch("adapters.groq.httpx.AsyncClient", return_value=client):
        await GroqAdapter().health_check("openai/gpt-oss-120b", "gsk-test", _BASE)
    _, kwargs = client.post.call_args
    assert kwargs["json"]["max_tokens"] >= 200


@pytest.mark.asyncio
async def test_probe_content_and_reasoning_both_empty_is_down():
    response = httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
    with patch("adapters.groq.httpx.AsyncClient", return_value=_client(response)):
        info = await GroqAdapter().health_check("qwen/qwen3.8-27b", "gsk-test", _BASE)
    assert info.status == "down"
    assert info.error_code == "empty_response"
