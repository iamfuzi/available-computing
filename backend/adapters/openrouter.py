import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers

_BASE = "https://openrouter.ai/api/v1"


def _infer_category(model: dict) -> str:
    arch = model.get("architecture", {})
    modality = arch.get("modality", "")
    if "audio" in modality:
        return "audio"
    modality_in = arch.get("input_modalities", [])
    if "image" in modality_in or "video" in modality_in:
        return "vision"
    mid = model.get("id", "").lower()
    if "embed" in mid:
        return "embedding"
    if "code" in mid or "coder" in mid:
        return "code"
    return "text"


class OpenRouterAdapter(ProviderAdapter):

    @property
    def provider_id(self) -> str:
        return "openrouter"

    @property
    def display_name(self) -> str:
        return "OpenRouter"

    @property
    def default_base_url(self) -> str:
        return _BASE

    async def validate_key(self, key: str, base_url: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code == 401:
                raise ValueError("Invalid API key")
            r.raise_for_status()

    async def list_models(self, key: str, base_url: str) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("data", []):
            models.append(ModelInfo(
                model_id=m.get("id", ""),
                display_name=m.get("name", m.get("id", "")),
                category=_infer_category(m),
                context_length=m.get("context_length"),
                raw=m,
            ))
        return models

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        pricing = model.raw.get("pricing", {})
        prompt_price = pricing.get("prompt", "1")
        completion_price = pricing.get("completion", "1")
        if prompt_price == "0" and completion_price == "0":
            return {"is_free": True, "free_type": "permanent"}
        return None

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "你是什么模型"}],
            "max_tokens": 20,
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "HTTP-Referer": "https://github.com/iamfuzi/available-computing",
                    },
                    json=payload,
                )
        except httpx.TimeoutException:
            return HealthInfo(status="down", response_ms=PROBE_TIMEOUT_SECONDS * 1000, error_code="timeout")
        except httpx.RequestError:
            return HealthInfo(status="down", response_ms=0, error_code="network_error")

        response_ms = int((time.monotonic() - start) * 1000)

        if r.status_code == 200:
            try:
                content = r.json()["choices"][0]["message"]["content"]
                if not content or not content.strip():
                    return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            except (KeyError, IndexError, TypeError):
                return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(
                status=status, response_ms=response_ms,
                observed_rate_limit=parse_rate_limit_headers(r),
                observed_remaining=parse_remaining_headers(r),
            )
        if r.status_code == 429:
            # 429 means the model is online but currently rate-limited — it's not
            # down. Mark it slow so it stays in the pool at lower priority rather
            # than being excluded entirely.
            return HealthInfo(status="slow", response_ms=response_ms, error_code="rate_limited",
                              observed_rate_limit=parse_rate_limit_headers(r),
                              observed_remaining=parse_remaining_headers(r))
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="down", response_ms=response_ms, error_code="server_error")
