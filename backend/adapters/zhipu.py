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
        if _infer_category(model_id) == "image":
            return await self._health_check_image(model_id, key, base_url)

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
                # ZhiPu may return content as a string or as a list of
                # multimodal parts ([{"type":"text","text":"..."}]).
                if isinstance(content, list):
                    text = " ".join(
                        p.get("text", "") for p in content if isinstance(p, dict)
                    )
                else:
                    text = content or ""
                if not text.strip():
                    return HealthInfo(status="down", response_ms=response_ms, error_code="empty_response")
            except (KeyError, IndexError, TypeError, AttributeError):
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
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")

    async def _health_check_image(self, model_id: str, key: str, base_url: str) -> HealthInfo:
        """Probe CogView models through their image-generation endpoint.

        Image generation normally takes longer than a chat heartbeat, so give it
        a wider timeout. A valid returned URL proves the model is callable; its
        latency may still classify it as ``slow`` without removing it from the
        image routing pool.
        """
        payload = {
            "model": model_id,
            "prompt": "白色背景上的一个蓝色圆点",
            "quality": "standard",
            "size": "1024x1024",
        }
        timeout_seconds = max(PROBE_TIMEOUT_SECONDS, 60)
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                r = await client.post(
                    f"{base_url}/images/generations",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
        except httpx.TimeoutException:
            return HealthInfo(
                status="slow",
                response_ms=timeout_seconds * 1000,
                error_code="timeout",
            )
        except httpx.RequestError:
            return HealthInfo(status="slow", response_ms=0, error_code="network_error")

        response_ms = int((time.monotonic() - start) * 1000)
        rate_fields = {
            "observed_rate_limit": parse_rate_limit_headers(r),
            "observed_remaining": parse_remaining_headers(r),
        }
        if r.status_code == 200:
            try:
                image_url = r.json()["data"][0]["url"]
                if not isinstance(image_url, str) or not image_url.strip():
                    raise ValueError("empty image URL")
            except (KeyError, IndexError, TypeError, ValueError):
                return HealthInfo(
                    status="down",
                    response_ms=response_ms,
                    error_code="empty_response",
                )
            status = "healthy" if response_ms < SLOW_RESPONSE_THRESHOLD_MS else "slow"
            return HealthInfo(status=status, response_ms=response_ms, **rate_fields)
        if r.status_code == 429:
            return HealthInfo(
                status="slow",
                response_ms=response_ms,
                error_code="rate_limited",
                **rate_fields,
            )
        if r.status_code in (401, 403):
            return HealthInfo(status="down", response_ms=response_ms, error_code="auth_failed")
        if r.status_code == 404:
            return HealthInfo(status="down", response_ms=response_ms, error_code="not_found")
        return HealthInfo(status="slow", response_ms=response_ms, error_code="server_error")
