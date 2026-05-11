import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers

_BASE = "https://open.bigmodel.cn/api/paas/v4"


def _infer_category(model_id: str) -> str:
    lower = model_id.lower()
    if "cogvideo" in lower:
        return "video"
    if "cogview" in lower:
        return "image"
    if "v-flash" in lower or "vision" in lower:
        return "vision"
    return "text"


class ZhiPuAdapter(ProviderAdapter):

    @property
    def provider_id(self) -> str:
        return "zhipu"

    @property
    def display_name(self) -> str:
        return "智谱AI (ZhiPu)"

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
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json()

        models = []
        seen = set()
        for m in data.get("data", []):
            model_id = m.get("id", "")
            seen.add(model_id)
            models.append(ModelInfo(
                model_id=model_id,
                display_name=model_id,
                category=_infer_category(model_id),
                raw=m,
            ))

        # ZhiPu /v4/models omits flash models — supplement from whitelist
        from services.whitelist import whitelist
        for entry in (self._whitelist_free_models() or []):
            if entry["id"] not in seen:
                seen.add(entry["id"])
                models.append(ModelInfo(
                    model_id=entry["id"],
                    display_name=entry["id"],
                    category=entry.get("category") or _infer_category(entry["id"]),
                    raw={"id": entry["id"], "source": "whitelist"},
                ))

        return models

    @staticmethod
    def _whitelist_free_models() -> list[dict]:
        from services.whitelist import whitelist
        provider = whitelist._data.get("providers", {}).get("zhipu", {})
        return provider.get("free_models", [])

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        # ZhiPu doesn't expose pricing in the models API; rely on whitelist
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
                    headers={"Authorization": f"Bearer {key}"},
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
            return HealthInfo(status="down", response_ms=response_ms, error_code="rate_limited",
                              observed_rate_limit=parse_rate_limit_headers(r),
                              observed_remaining=parse_remaining_headers(r))
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="down", response_ms=response_ms, error_code="server_error")
