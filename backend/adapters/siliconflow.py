import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers


def _infer_category(model_id: str) -> str:
    lower = model_id.lower()
    if any(k in lower for k in ("embedding", "bge", "e5")):
        return "embedding"
    if any(k in lower for k in ("vision", "vl", "internvl", "qwen-vl")):
        return "vision"
    if any(k in lower for k in ("coder", "code", "deepseek-coder")):
        return "code"
    return "text"


class SiliconFlowAdapter(ProviderAdapter):

    @property
    def provider_id(self) -> str:
        return "siliconflow"

    @property
    def display_name(self) -> str:
        return "硅基流动"

    @property
    def default_base_url(self) -> str:
        return "https://api.siliconflow.cn/v1"

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
        models = []
        page = 1
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                r = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {key}"},
                    params={"page": page, "page_size": 100, "type": "text"},
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("data", [])
                if not items:
                    break
                for m in items:
                    model_id = m.get("id", "")
                    models.append(ModelInfo(
                        model_id=model_id,
                        display_name=m.get("name", model_id),
                        category=_infer_category(model_id),
                        context_length=m.get("context_length"),
                        raw=m,
                    ))
                if len(items) < 100:
                    break
                page += 1
        return models

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        # SiliconFlow marks free models with pricing fields or tags in the raw response
        raw = model.raw
        pricing = raw.get("pricing") or {}
        if pricing.get("input") == "0" or pricing.get("input") == 0:
            return {"is_free": True, "free_type": "permanent"}
        tags = raw.get("tags", [])
        if "free" in tags or "免费" in tags:
            return {"is_free": True, "free_type": "permanent"}
        return None

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
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
