import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers

_BASE = "https://openrouter.ai/api/v1"

# Reasoning-style :free models routinely spend all tokens of a small probe on
# the ``reasoning`` field and return an empty ``content``. 20 tokens made every
# such model look dead ("empty_response"); 200 is enough for reasoning + a
# short answer on the models observed in production.
PROBE_MAX_TOKENS = 200

# 401/403 error-body fragments that describe a *model-level* access policy
# (region lock, client-type gate, data-policy filter) rather than a dead key.
# The key itself is fine — these must not flip the channel to key_invalid.
_ACCESS_RESTRICTED_MARKERS = (
    "not available in your region",
    "only available on agentic harnesses",
    "matching your data policy",
    "no allowed providers",
)


def _error_message(response: httpx.Response) -> str:
    """Best-effort lowercase error.message from an error response body."""
    try:
        err = response.json().get("error")
    except ValueError:
        return ""
    if isinstance(err, dict):
        return str(err.get("message") or "").lower()
    return str(err or "").lower()


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
        try:
            prompt_price = float(pricing.get("prompt", 1))
            completion_price = float(pricing.get("completion", 1))
            if prompt_price == 0.0 and completion_price == 0.0:
                return {"is_free": True, "free_type": "permanent"}
            return {"is_free": False}
        except (ValueError, TypeError):
            return None

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "你是什么模型"}],
            "max_tokens": PROBE_MAX_TOKENS,
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
            # Transient — a slow/queued provider (notably OpenRouter :free
            # models) shouldn't be ejected from the pool on a single timeout.
            return HealthInfo(status="slow", response_ms=PROBE_TIMEOUT_SECONDS * 1000, error_code="timeout")
        except httpx.RequestError:
            # Network blips are transient too; keep the model at lower priority
            # rather than marking it down.
            return HealthInfo(status="slow", response_ms=0, error_code="network_error")

        response_ms = int((time.monotonic() - start) * 1000)

        if r.status_code == 200:
            try:
                message = r.json()["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                reasoning = (message.get("reasoning") or "").strip()
                # Reasoning models with a truncated budget answer only in the
                # reasoning field; that is still proof the model is serving.
                if not content and not reasoning:
                    return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            except (KeyError, IndexError, TypeError, ValueError):
                return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(
                status=status, response_ms=response_ms,
                observed_rate_limit=parse_rate_limit_headers(r),
                observed_remaining=parse_remaining_headers(r),
            )
        if r.status_code == 429:
            # Two distinct sources with different remedies: our own platform
            # quota (error.metadata.error_type == "rate_limit_exceeded") needs
            # a cooldown and less probing; upstream provider overload (a
            # provider_code in the metadata) is unrelated to our quota and
            # only deprioritizes the model.
            metadata: dict = {}
            try:
                err = r.json().get("error")
                if isinstance(err, dict):
                    metadata = err.get("metadata") or {}
            except ValueError:
                pass
            error_code = "upstream_rate_limited" if metadata.get("provider_code") else "rate_limited"
            return HealthInfo(status="slow", response_ms=response_ms, error_code=error_code,
                              observed_rate_limit=parse_rate_limit_headers(r),
                              observed_remaining=parse_remaining_headers(r))
        if r.status_code in (401, 403):
            # Region locks / client-type gates / data-policy filters are model
            # access restrictions — the credential still works for other
            # models. Only a genuine auth rejection stays auth_failed (the
            # service layer then re-verifies via validate_key before blaming
            # the channel key).
            if any(marker in _error_message(r) for marker in _ACCESS_RESTRICTED_MARKERS):
                return HealthInfo(status="down", response_ms=response_ms, error_code="access_restricted")
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        # 5xx is almost always a transient upstream issue (OpenRouter is an
        # aggregator, so a single backing provider error shouldn't drop the
        # model out of the pool). Mark slow so it stays available at lower
        # priority and recovers on the next probe.
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")
