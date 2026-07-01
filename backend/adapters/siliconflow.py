import time
from typing import Optional
import httpx
from .base import ProviderAdapter, ModelInfo, HealthInfo
from config import PROBE_TIMEOUT_SECONDS, SLOW_RESPONSE_THRESHOLD_MS
from services.rate_limit import parse_rate_limit_headers, parse_remaining_headers


def _infer_category(model_id: str) -> str:
    lower = model_id.lower()
    if "rerank" in lower:
        return "rerank"
    if any(k in lower for k in ("embedding", "bge", "e5")):
        return "embedding"
    if any(k in lower for k in (
        "vision",
        "ocr",
        "captioner",
        "image-edit",
        "qwen-image",
        "omni",
        "vl",
        "internvl",
        "qwen-vl",
        "glm-4v",
        "glm-4.1v",
        "glm-4.5v",
    )):
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
        seen = set()
        async with httpx.AsyncClient(timeout=15) as client:
            for model_type in ("text", "embedding", "rerank"):
                page = 1
                while True:
                    r = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {key}"},
                        params={"page": page, "page_size": 100, "type": model_type},
                    )
                    r.raise_for_status()
                    data = r.json()
                    items = data.get("data", [])
                    if not items:
                        break
                    for m in items:
                        model_id = m.get("id", "")
                        if model_id in seen:
                            continue
                        seen.add(model_id)
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

    async def fetch_free_model_ids(self, key: str, base_url: str) -> Optional[set[str]]:
        """Fetch the authoritative free-model set via the charging_type filter.

        SiliconFlow's /v1/models endpoint accepts a ``charging_type=free``
        parameter that returns exactly the currently-free models. This is the
        only reliable signal (the API exposes no per-model pricing field, and
        the free catalog changes over time as models move between tiers).
        """
        free_ids: set[str] = set()
        async with httpx.AsyncClient(timeout=15) as client:
            # The endpoint paginates by type; query each type with the free
            # filter to build the complete free set.
            for model_type in ("text", "multimodal", "embedding", "rerank", "audio", "image", "video"):
                page = 1
                while True:
                    try:
                        r = await client.get(
                            f"{base_url}/models",
                            headers={"Authorization": f"Bearer {key}"},
                            params={"charging_type": "free", "type": model_type, "page": page, "page_size": 100},
                        )
                    except (httpx.RequestError, httpx.HTTPStatusError):
                        # Network/server error: bail out and let the caller fall
                        # back to other detection methods rather than returning a
                        # partial (and therefore wrong) free set.
                        return None
                    if r.status_code != 200:
                        # If the filter param isn't supported, this provider
                        # version can't give us a free list.
                        return None
                    items = r.json().get("data") or []
                    for it in items:
                        mid = it.get("id")
                        if mid:
                            free_ids.add(mid)
                    if len(items) < 100:
                        break
                    page += 1
        return free_ids

    def detect_free_from_api(self, model: ModelInfo) -> Optional[dict]:
        # SiliconFlow official convention: paid variants carry a "Pro/" prefix
        # while free variants keep the original name (e.g. "Qwen/Qwen2.5-7B-Instruct"
        # is free, "Pro/Qwen/Qwen2.5-7B-Instruct" is paid). The /v1/models API
        # exposes no pricing field, so this prefix rule is the only deterministic
        # signal available.
        if model.model_id.startswith("Pro/"):
            return {"is_free": False, "free_type": "permanent", "free_source": "prefix_rule"}
        # Fall back to API fields for forward-compatibility (in case SiliconFlow
        # starts returning pricing/tags in the future).
        raw = model.raw
        pricing = raw.get("pricing") or {}
        if pricing.get("input") == "0" or pricing.get("input") == 0:
            return {"is_free": True, "free_type": "permanent"}
        tags = raw.get("tags", [])
        if "free" in tags or "免费" in tags:
            return {"is_free": True, "free_type": "permanent"}
        return None

    async def health_check(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        cat = _infer_category(model_id)
        if cat == "embedding":
            return await self._health_check_embedding(model_id, key, base_url)
        if cat == "rerank":
            return await self._health_check_rerank(model_id, key, base_url)
        return await self._health_check_chat(model_id, key, base_url)

    async def _health_check_embedding(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {"model": model_id, "input": "hello"}
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f"{base_url}/embeddings",
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
                data = r.json()
                if not data.get("data") or not data["data"][0].get("embedding"):
                    return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            except (KeyError, IndexError, TypeError):
                return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(status=status, response_ms=response_ms)
        if r.status_code == 429:
            # Rate-limited, not down — keep the model in the pool at lower priority.
            return HealthInfo(status="slow", response_ms=response_ms, error_code="rate_limited")
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")

    async def _health_check_rerank(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        payload = {"model": model_id, "query": "hello", "documents": ["hi", "world"]}
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f"{base_url}/rerank",
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
                data = r.json()
                if not data.get("results"):
                    return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            except (KeyError, TypeError):
                return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(status=status, response_ms=response_ms)
        if r.status_code == 429:
            # Rate-limited, not down — keep the model in the pool at lower priority.
            return HealthInfo(status="slow", response_ms=response_ms, error_code="rate_limited")
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")

    async def _health_check_chat(self, model_id: str, key: str, base_url: str) -> HealthInfo:
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
            return HealthInfo(status="slow", response_ms=PROBE_TIMEOUT_SECONDS * 1000, error_code="timeout")
        except httpx.RequestError:
            return HealthInfo(status="slow", response_ms=0, error_code="network_error")

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
            # Rate-limited, not down — keep the model in the pool at lower priority.
            return HealthInfo(status="slow", response_ms=response_ms, error_code="rate_limited",
                              observed_rate_limit=parse_rate_limit_headers(r),
                              observed_remaining=parse_remaining_headers(r))
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")
