import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers

# Gemini uses a non-OpenAI models endpoint format
_GEMINI_LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_GEMINI_CHAT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _infer_category(model_name: str) -> str:
    lower = model_name.lower()
    if "embedding" in lower:
        return "embedding"
    if "vision" in lower:
        return "vision"
    return "text"


class GeminiAdapter(ProviderAdapter):

    @property
    def provider_id(self) -> str:
        return "gemini"

    @property
    def display_name(self) -> str:
        return "Google Gemini"

    @property
    def default_base_url(self) -> str:
        # Kept for interface consistency; Gemini uses its own endpoint
        return "https://generativelanguage.googleapis.com/v1beta"

    async def validate_key(self, key: str, base_url: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                _GEMINI_LIST_URL,
                params={"key": key, "pageSize": 1},
            )
            if r.status_code in (400, 403):
                raise ValueError("Invalid API key")
            r.raise_for_status()

    async def list_models(self, key: str, base_url: str) -> list[ModelInfo]:
        models = []
        page_token = None
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                params = {"key": key, "pageSize": 50}
                if page_token:
                    params["pageToken"] = page_token
                r = await client.get(_GEMINI_LIST_URL, params=params)
                r.raise_for_status()
                data = r.json()

                for m in data.get("models", []):
                    name = m.get("name", "")          # "models/gemini-2.0-flash"
                    model_id = name.removeprefix("models/")
                    # Only include models that support generateContent
                    supported = m.get("supportedGenerationMethods", [])
                    if "generateContent" not in supported:
                        continue
                    models.append(ModelInfo(
                        model_id=model_id,
                        display_name=m.get("displayName", model_id),
                        category=_infer_category(model_id),
                        context_length=m.get("inputTokenLimit"),
                        raw=m,
                    ))

                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return models

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        # Gemini API doesn't expose pricing; rely on whitelist
        return None

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        url = _GEMINI_CHAT_URL.format(model=model_id)
        payload = {
            "contents": [{"parts": [{"text": "你是什么模型"}]}],
            "generationConfig": {"maxOutputTokens": 20},
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                r = await client.post(url, params={"key": key}, json=payload)
        except httpx.TimeoutException:
            return HealthInfo(status="slow", response_ms=PROBE_TIMEOUT_SECONDS * 1000, error_code="timeout")
        except httpx.RequestError:
            return HealthInfo(status="slow", response_ms=0, error_code="network_error")

        response_ms = int((time.monotonic() - start) * 1000)

        if r.status_code == 200:
            try:
                text = ""
                for candidate in r.json()["candidates"]:
                    for part in candidate.get("content", {}).get("parts", []):
                        if "text" in part:
                            text = part["text"]
                            break
                    if text:
                        break
                if not text or not text.strip():
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
            # 429 means the model is online but currently rate-limited — it's
            # not down. Mark it slow so it stays in the pool at lower priority
            # rather than being excluded entirely.
            return HealthInfo(status="slow", response_ms=response_ms, error_code="rate_limited",
                              observed_rate_limit=parse_rate_limit_headers(r),
                              observed_remaining=parse_remaining_headers(r))
        if r.status_code in (400, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")
