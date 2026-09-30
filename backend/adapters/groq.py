import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers

_CATEGORY_MARKERS = (
    # audio endpoints (transcription / TTS) — never chat-routable
    ("whisper", "audio"),
    ("orpheus", "audio"),
    ("canopylabs", "audio"),
    ("playai", "audio"),
    ("tts", "audio"),
    # content-safety classifiers — callable via chat but semantically wrong
    # for general routing; keep them out of the text pool
    ("prompt-guard", "guard"),
    ("safeguard", "guard"),
)


def _infer_category(model_id: str) -> str:
    lower = model_id.lower()
    for marker, category in _CATEGORY_MARKERS:
        if marker in lower:
            return category
    if "vision" in lower or "llava" in lower:
        return "vision"
    return "text"


class GroqAdapter(ProviderAdapter):

    @property
    def provider_id(self) -> str:
        return "groq"

    @property
    def display_name(self) -> str:
        return "Groq"

    @property
    def default_base_url(self) -> str:
        return "https://api.groq.com/openai/v1"

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
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            models.append(ModelInfo(
                model_id=model_id,
                display_name=model_id,
                category=_infer_category(model_id),
                context_length=m.get("context_window"),
                raw=m,
            ))
        return models

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        # Groq doesn't expose pricing in the models API; rely on whitelist
        return None

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "你是什么模型"}],
            # gpt-oss models reason first and return empty content when the
            # budget is small (measured 2026-09-30: mt=20 → content='',
            # reasoning filled; mt=200 → normal answer).
            "max_tokens": 200,
        }
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            return HealthInfo(status="slow", response_ms=PROBE_TIMEOUT_SECONDS * 1000, error_code="timeout")
        except httpx.RequestError:
            return HealthInfo(status="slow", response_ms=0, error_code="network_error")

        response_ms = int((time.monotonic() - start) * 1000)

        if r.status_code == 200:
            try:
                message = r.json()["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                reasoning = (message.get("reasoning") or "").strip()
                # Reasoning models with a tight budget answer only in the
                # reasoning field; that is still proof the model is serving.
                if not content and not reasoning:
                    return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            except (KeyError, IndexError, TypeError):
                return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(
                status=status, response_ms=response_ms,
                observed_rate_limit=self._groq_rate_limits(r),
                observed_remaining=self._groq_remaining(r),
            )
        if r.status_code == 429:
            # 429 means the model is online but currently rate-limited — it's
            # not down. Mark it slow so it stays in the pool at lower priority
            # rather than being excluded entirely.
            return HealthInfo(status="slow", response_ms=response_ms, error_code="rate_limited",
                              observed_rate_limit=self._groq_rate_limits(r),
                              observed_remaining=self._groq_remaining(r))
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")

    @staticmethod
    def _groq_rate_limits(r) -> dict | None:
        """Groq's x-ratelimit-limit-requests is per-DAY, not per-minute
        (measured 2026-09-30: 1000 = the free-tier RPD, alongside TPM 8000 in
        limit-tokens). Re-key it as rpd so budgets don't read it as rpm=1000.
        """
        limits = parse_rate_limit_headers(r)
        if limits and "rpm" in limits:
            limits["rpd"] = limits.pop("rpm")
        return limits

    @staticmethod
    def _groq_remaining(r) -> dict | None:
        remaining = parse_remaining_headers(r)
        if remaining and "rpm_remaining" in remaining:
            remaining["rpd_remaining"] = remaining.pop("rpm_remaining")
        return remaining
