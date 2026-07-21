"""Generic adapter for conservatively configured OpenAI-compatible APIs."""

import time
from typing import Any, Optional

import httpx

from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers

from .base import HealthInfo, ModelInfo, ProviderAdapter
from .declarative_config import DeclarativeProviderConfig


def _get_path(value: Any, path: Optional[str], default=None):
    if not path:
        return default
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _infer_category(model_id: str, raw: dict, config: DeclarativeProviderConfig) -> str:
    mapping = config.model_mapping
    configured = _get_path(raw, mapping.category_path)
    if configured in {"text", "vision", "code", "embedding", "audio", "rerank"}:
        return configured
    if bool(_get_path(raw, mapping.vision_capability_path, False)):
        return "vision"
    lower = model_id.lower()
    if "embed" in lower:
        return "embedding"
    if "rerank" in lower:
        return "rerank"
    if "vision" in lower or "pixtral" in lower:
        return "vision"
    if "code" in lower or "coder" in lower or "codestral" in lower:
        return "code"
    if "audio" in lower or "whisper" in lower:
        return "audio"
    return "text"


class DeclarativeAdapter(ProviderAdapter):
    def __init__(self, config: DeclarativeProviderConfig):
        self.config = config
        self._free_model_ids = set(config.free_detection.model_ids)

    @property
    def provider_id(self) -> str:
        return self.config.id

    @property
    def display_name(self) -> str:
        return self.config.name

    @property
    def default_base_url(self) -> str:
        return self.config.base_url

    def _headers(self, key: str) -> dict[str, str]:
        if self.config.auth.type == "none":
            return {}
        return {self.config.auth.header: f"Bearer {key}"}

    def request_headers(self, key: str) -> dict[str, str]:
        return self._headers(key)

    @property
    def requires_api_key(self) -> bool:
        return self.config.auth.type != "none"

    @staticmethod
    def _url(base_url: str, endpoint: str) -> str:
        return f"{base_url.rstrip('/')}{endpoint}"

    async def validate_key(self, key: str, base_url: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                self._url(base_url, self.config.endpoints.models),
                headers=self._headers(key),
            )
        if response.status_code in (401, 403):
            raise ValueError("Invalid API key")
        response.raise_for_status()

    async def list_models(self, key: str, base_url: str) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                self._url(base_url, self.config.endpoints.models),
                headers=self._headers(key),
            )
            response.raise_for_status()
            payload = response.json()

        items = _get_path(payload, self.config.model_mapping.items_path, [])
        if not isinstance(items, list):
            raise ValueError(
                f"Invalid model catalog from {self.provider_id}: expected a list at "
                f"{self.config.model_mapping.items_path}"
            )

        models: list[ModelInfo] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            model_id = _get_path(raw, self.config.model_mapping.id_path)
            if not isinstance(model_id, str) or not model_id:
                continue
            override = self.config.model_overrides.get(model_id)
            display_name = _get_path(raw, self.config.model_mapping.display_name_path, model_id)
            context_length = _get_path(raw, self.config.model_mapping.context_length_path)
            category = _infer_category(model_id, raw, self.config)
            if override:
                display_name = override.display_name or display_name
                context_length = override.context_length or context_length
                category = override.category or category
            models.append(ModelInfo(
                model_id=model_id,
                display_name=display_name if isinstance(display_name, str) else model_id,
                category=category,
                context_length=context_length if isinstance(context_length, int) else None,
                rate_limit=override.rate_limit if override else None,
                raw=raw,
            ))
        return models

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        detection = self.config.free_detection
        is_free = model.model_id in self._free_model_ids
        if detection.method == "id_suffix":
            is_free = is_free or model.model_id.endswith(detection.id_suffix or "")
        if is_free:
            return {
                "is_free": True,
                "free_type": detection.free_type,
                "free_source": f"declarative_{detection.method}",
            }
        # A declarative provider is only as trustworthy as its reviewed
        # allowlist. Everything else is explicitly non-free for routing.
        return {"is_free": False, "free_source": f"declarative_{detection.method}"}

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": self.config.probe.prompt}],
            "max_tokens": self.config.probe.max_tokens,
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self._url(base_url, self.config.endpoints.chat_completions),
                    headers=self._headers(key),
                    json=payload,
                )
        except httpx.TimeoutException:
            return HealthInfo(
                status="slow",
                response_ms=PROBE_TIMEOUT_SECONDS * 1000,
                error_code="timeout",
            )
        except httpx.RequestError:
            return HealthInfo(status="slow", response_ms=0, error_code="network_error")

        response_ms = int((time.monotonic() - start) * 1000)
        rate_limit = parse_rate_limit_headers(response)
        remaining = parse_remaining_headers(response)
        if response.status_code == 200:
            content = _get_path(response.json(), "choices")
            try:
                message = content[0]["message"]
                # Reasoning models can spend a small probe's entire output
                # budget in a reasoning field and legitimately return an
                # empty final content string. Either field proves inference
                # succeeded; a truly empty message still fails the probe.
                content = message.get("content") or message.get("reasoning")
            except (IndexError, KeyError, TypeError):
                content = None
            if not isinstance(content, str) or not content.strip():
                return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(
                status=status,
                response_ms=response_ms,
                observed_rate_limit=rate_limit,
                observed_remaining=remaining,
            )
        if response.status_code == 429:
            return HealthInfo(
                status="slow",
                response_ms=response_ms,
                error_code="rate_limited",
                observed_rate_limit=rate_limit,
                observed_remaining=remaining,
            )
        if response.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if response.status_code == 402:
            return HealthInfo(status="down", response_ms=response_ms, error_code="payment_required")
        if response.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")
