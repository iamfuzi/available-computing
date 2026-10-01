import json
import time
import logging
import httpx
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from sqlmodel import Session, select
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional

from database import get_session, engine
from models import Model, Channel, HealthRecord, ApiKey
from api.auth import verify_token_or_apikey
from services.health import (
    record_passive_health,
    record_billing_failure,
    record_channel_billing_failure,
    clear_billing_failures,
    record_rate_limit,
    clear_rate_limit,
)
from services.event_recheck import trigger_event_recheck
from services import errors
from api.middleware import get_request_id, REQUEST_ID_HEADER
# Routing logic lives in services.router (policy, candidate generation, scoring,
# model matching, fallback ordering). The HTTP fallback loop, rate limiting,
# streaming, and health feedback remain in this module. Aliases keep the many
# internal call sites stable without a large rewrite.
from services.router import (
    AUTO_RE as _AUTO_RE,
    RoutingPolicy,
    apply_routing_policy as _apply_routing_policy,
    auto_candidate_models as _auto_candidate_models,
    channel_route_eligible as _channel_route_eligible,
    classify_auto_route_unavailability as _classify_auto_route_unavailability,
    effective_routing_policy as _effective_routing_policy,
    is_profile_authorized as _is_profile_authorized,
    load_profile as _load_profile,
    model_route_eligible as _model_route_eligible,
    request_candidate_models as _request_candidate_models,
    resolve_auto_category_model as _resolve_auto_category_model,
    resolve_category_model as _resolve_category_model,
    resolve_fast_model as _resolve_fast_model,  # noqa: F401 — re-exported for tests
    resolve_model as _resolve_model,  # noqa: F401 — re-exported for tests
    resolve_smart_model as _resolve_smart_model,  # noqa: F401 — re-exported for tests
    single_route_candidates as _single_route_candidates,
    try_bind_model as _try_bind_model,
    category_candidates as _category_candidates,
)
from services.router.scoring import (
    is_cooling_down as _is_cooling_down,
    is_pool_eligible as _is_pool_eligible,
    recent_success_rate as _recent_success_rate,
    route_score_key as _route_score_key,
)
from config import (
    PROXY_RATE_WINDOW_SECONDS,
    PROXY_API_KEY_RATE_LIMIT,
    PROXY_ADMIN_RATE_LIMIT,
    PROXY_IP_FALLBACK_RATE_LIMIT,
    PROXY_MODEL_CONCURRENCY_LIMIT,
    PROXY_EMBEDDING_CONCURRENCY_LIMIT,
    PROXY_SLOT_QUEUE_TIMEOUT_SECONDS,
    PROXY_DEFAULT_MODEL_RPM,
    PROXY_PROVIDER_RPM,
    PROXY_PASSTHROUGH_TIMEOUT_SECONDS,
)

router = APIRouter()

logger = logging.getLogger(__name__)

# Maximum upstream attempts within a single request's fallback chain. Kept in
# the proxy module (not the router package) because it bounds the HTTP loop.
_MAX_UPSTREAM_ATTEMPTS = 50

# Transient statuses spend the next candidate's quota immediately. Non-
# transient rejections (400/401/403/404…) are ALSO walked down the chain:
# the most common 400 cause in the free pool is a model-specific constraint
# (max_tokens over a model's output cap, unsupported params) that other
# candidates accept fine. The exhausted tail replays the first rejection
# verbatim when no candidate accepts the request, so true caller errors still
# surface with their real upstream status.
_RETRYABLE_UPSTREAM_STATUSES = {408, 429, 500, 502, 503, 504}

_proxy_requests: dict[str, list[float]] = {}
_model_semaphores: dict[str, asyncio.Semaphore] = {}


class ProxyRateLimitExceeded(Exception):
    status_code = 429

    def __init__(self, retry_after: int, scope: str):
        self.retry_after = retry_after
        self.scope = scope
        super().__init__("Local proxy rate limit exceeded")


class ModelBudgetExceeded(Exception):
    def __init__(self, retry_after: int, reason: str):
        self.retry_after = retry_after
        self.reason = reason
        super().__init__(reason)


def _rate_subject(ip: str, auth_header: str | None) -> tuple[str, int]:
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token.startswith("ac_"):
            digest = hashlib.sha256(token.encode()).hexdigest()[:16]
            return f"apikey:{digest}", PROXY_API_KEY_RATE_LIMIT
        return "jwt:admin", PROXY_ADMIN_RATE_LIMIT
    # Compatibility path for direct unit tests and unauthenticated preflight.
    return f"ip:{ip}", 60


def _check_ip_fallback_rate_limit(ip: str):
    now = time.time()
    scope = f"ip-fallback:{ip}"
    attempts = _proxy_requests.get(scope, [])
    attempts = [t for t in attempts if now - t < PROXY_RATE_WINDOW_SECONDS]
    _proxy_requests[scope] = attempts
    if len(attempts) >= PROXY_IP_FALLBACK_RATE_LIMIT:
        raise ProxyRateLimitExceeded(PROXY_RATE_WINDOW_SECONDS, scope)
    _proxy_requests.setdefault(scope, []).append(now)


def _check_proxy_rate_limit(
    ip: str,
    route: str = "*",
    auth_header: str | None = None,
    api_key: ApiKey | None = None,
):
    now = time.time()
    subject, limit = _rate_subject(ip, auth_header)
    scope = f"{subject}:route:{route}"
    attempts = _proxy_requests.get(scope, [])
    attempts = [t for t in attempts if now - t < PROXY_RATE_WINDOW_SECONDS]
    _proxy_requests[scope] = attempts
    if len(attempts) >= limit:
        raise ProxyRateLimitExceeded(PROXY_RATE_WINDOW_SECONDS, scope)
    _proxy_requests.setdefault(scope, []).append(now)
    # Keep a broad IP fallback as abuse protection, but make it loose enough
    # that different third-party API keys behind one NAT are not coupled.
    if auth_header is not None:
        _check_ip_fallback_rate_limit(ip)
    if api_key is not None:
        _check_api_key_policy_rate_limit(api_key)


def _check_api_key_policy_rate_limit(api_key: ApiKey):
    """Enforce a key's aggregate RPM/RPD across every proxy route."""
    if not api_key.rate_limit_rpm and not api_key.rate_limit_rpd:
        return
    now = time.time()
    scope = f"apikey-policy:{api_key.id}"
    day_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    attempts = [stamp for stamp in _proxy_requests.get(scope, []) if stamp >= day_start]
    if api_key.rate_limit_rpm:
        recent = sum(1 for stamp in attempts if now - stamp < 60)
        if recent >= api_key.rate_limit_rpm:
            raise ProxyRateLimitExceeded(60, f"{scope}:rpm")
    if api_key.rate_limit_rpd and len(attempts) >= api_key.rate_limit_rpd:
        raise ProxyRateLimitExceeded(
            max(1, int(day_start + 86400 - now)),
            f"{scope}:rpd",
        )
    attempts.append(now)
    _proxy_requests[scope] = attempts


def _model_slot_key(channel: Channel, model: Model, category: str = "chat") -> str:
    return f"{category}:{channel.provider_type}:{channel.id}:{model.model_id}"


def _slot_limit(category: str) -> int:
    if category in ("embedding", "rerank"):
        return max(1, PROXY_EMBEDDING_CONCURRENCY_LIMIT)
    return max(1, PROXY_MODEL_CONCURRENCY_LIMIT)


async def _try_acquire_model_slot(
    channel: Channel, model: Model, category: str = "chat"
) -> tuple[str, bool]:
    """Acquire a per-(category, channel, model) concurrency slot.

    Requests queue for up to PROXY_SLOT_QUEUE_TIMEOUT_SECONDS instead of
    failing fast: embedding bursts from a single caller used to mass-503
    against a limit of 2, even though every request would have succeeded
    a fraction of a second later. Fail-fast is kept for a zero timeout.
    """
    key = _model_slot_key(channel, model, category)
    sem = _model_semaphores.setdefault(key, asyncio.Semaphore(_slot_limit(category)))
    if PROXY_SLOT_QUEUE_TIMEOUT_SECONDS <= 0:
        if getattr(sem, "_value", 0) <= 0:
            return key, False
        await sem.acquire()
        return key, True
    try:
        await asyncio.wait_for(
            sem.acquire(), timeout=PROXY_SLOT_QUEUE_TIMEOUT_SECONDS
        )
        return key, True
    except asyncio.TimeoutError:
        return key, False


def _release_model_slot(slot_key: str | None):
    if not slot_key:
        return
    sem = _model_semaphores.get(slot_key)
    if sem:
        sem.release()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: str


# RoutingPolicy is imported from services.router (see imports above) and used
# directly as the request-body model for routing_policy fields below.


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(
        ...,
        description=(
            "A concrete model id (e.g. 'meta-llama/llama-3.3-70b-instruct') "
            "or an auto-routing prefix:\n"
            "  • auto:smart — largest available model (by param size)\n"
            "  • auto:fast  — fastest available model (by latency)\n"
            "  • auto:text / auto:vision / auto:code — best model in a category"
        ),
    )
    messages: list[ChatMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = False
    stop: Optional[list[str]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    routing_policy: Optional[RoutingPolicy] = None


class EmbeddingRequest(BaseModel):
    """OpenAI-compatible embedding request.

    The proxy resolves ``model`` (concrete id only; no auto-routing) against the
    embedding candidate pool and forwards to the upstream ``/embeddings`` endpoint.
    """
    model_config = ConfigDict(extra="ignore")
    model: str = Field(
        ...,
        description="An embedding model id from GET /v1/models?category=embedding",
    )
    input: str | list[str] = Field(
        ...,
        description="A string or list of strings to embed",
    )
    encoding_format: Optional[str] = None


class RerankRequest(BaseModel):
    """Rerank request (SiliconFlow-compatible; not an OpenAI standard endpoint).

    The proxy resolves ``model`` against the rerank candidate pool and forwards
    to the upstream ``/rerank`` endpoint.
    """
    model_config = ConfigDict(extra="ignore")
    model: str = Field(
        ...,
        description="A rerank model id from GET /v1/models?category=rerank",
    )
    query: str
    documents: list[str]
    top_n: Optional[int] = None
    return_documents: Optional[bool] = None


class ImageGenerationRequest(BaseModel):
    """OpenAI-compatible image generation request for free image models."""

    model_config = ConfigDict(extra="ignore")
    model: str = Field(
        default="auto:image",
        description="A concrete image model id or auto:image",
    )
    prompt: str = Field(..., min_length=1, max_length=5000)
    n: Literal[1] = 1
    quality: Optional[Literal["standard", "hd"]] = None
    size: Optional[str] = Field(default=None, pattern=r"^\d+x\d+$")
    response_format: Literal["url"] = "url"
    user: Optional[str] = Field(default=None, min_length=6, max_length=128)
    watermark_enabled: Optional[bool] = None
    routing_policy: Optional[RoutingPolicy] = None


class SelfTestRequest(BaseModel):
    model: str = "auto:text"
    routing_policy: Optional[RoutingPolicy] = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_retry_after(headers: httpx.Headers) -> int | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except ValueError:
        return None


def _parse_rate_limit_json(model: Model) -> dict:
    if not model.rate_limit:
        return {}
    try:
        data = json.loads(model.rate_limit)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _passive_call_count(session: Session, model_id: str, since: datetime) -> int:
    return len(session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id == model_id)
        .where(HealthRecord.is_passive == True)
        .where(HealthRecord.checked_at >= since)
    ).all())


def _passive_channel_call_count(session: Session, channel_id: str, since: datetime) -> int:
    """Passive proxied calls across ALL models of one channel in the window.

    Models on the same provider share one upstream API key, whose real RPM
    cap applies to their combined volume — per-model counting cannot see it.
    """
    return len(session.exec(
        select(HealthRecord)
        .join(Model, HealthRecord.model_id == Model.id)
        .where(Model.channel_id == channel_id)
        .where(HealthRecord.is_passive == True)
        .where(HealthRecord.checked_at >= since)
    ).all())


# Per-channel provider RPM overrides (Setting key ``provider_rpm:<channel_id>``),
# cached briefly to keep the per-request budget check cheap. Free-tier limits
# vary wildly per provider (e.g. agnes ≈ 10 RPM), so PROXY_PROVIDER_RPM is
# only the global default and each channel can override it.
_provider_rpm_cache: dict[str, tuple[float, int | None]] = {}
_PROVIDER_RPM_CACHE_TTL_SECONDS = 30.0

# Channel-level token-per-minute tracking (soft cap via Setting
# provider_tpm:<channel_id>). Request-count limits can't protect providers
# whose binding quota is tokens — Groq's free tier allows only 8K TPM per
# model, so two large-context requests per minute can trip it while RPM sits
# idle. Non-streaming responses record the exact usage.total_tokens; streams
# estimate from SSE bytes (no usage object mid-stream) — an under-estimate is
# preferred over blocking legitimate traffic.
_provider_tpm_windows: dict[str, list[tuple[float, int]]] = {}
_PROVIDER_TPM_WINDOW_SECONDS = 60.0
_PROVIDER_TPM_SSE_BYTES_PER_TOKEN = 6  # JSON overhead incl., rough middle ground
_provider_tpm_cache: dict[str, tuple[float, int | None]] = {}


def _record_provider_tokens(channel_id: str, tokens: int) -> None:
    if tokens <= 0:
        return
    now = time.monotonic()
    window = [e for e in _provider_tpm_windows.get(channel_id, [])
              if e[0] > now - _PROVIDER_TPM_WINDOW_SECONDS]
    window.append((now, tokens))
    _provider_tpm_windows[channel_id] = window


def _provider_tpm_used(channel_id: str) -> int:
    now = time.monotonic()
    return sum(t for ts, t in _provider_tpm_windows.get(channel_id, [])
               if ts > now - _PROVIDER_TPM_WINDOW_SECONDS)


def _effective_provider_tpm(session: Session, channel_id: str) -> int | None:
    """Tokens-per-minute cap for a channel; None means unlimited."""
    now = time.monotonic()
    cached = _provider_tpm_cache.get(channel_id)
    if cached and cached[0] > now:
        return cached[1]
    from models import Setting

    row = session.get(Setting, f"provider_tpm:{channel_id}")
    try:
        tpm = int(row.value) if row and row.value not in (None, "") else None
    except (TypeError, ValueError):
        tpm = None
    if tpm is not None and tpm <= 0:
        tpm = None
    _provider_tpm_cache[channel_id] = (now + _PROVIDER_RPM_CACHE_TTL_SECONDS, tpm)
    return tpm


def _channel_rpm_override(session: Session, channel_id: str) -> int | None:
    now = time.monotonic()
    cached = _provider_rpm_cache.get(channel_id)
    if cached and cached[0] > now:
        return cached[1]
    from models import Setting

    row = session.get(Setting, f"provider_rpm:{channel_id}")
    raw = row.value if row else None
    try:
        rpm = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        rpm = None
    _provider_rpm_cache[channel_id] = (now + _PROVIDER_RPM_CACHE_TTL_SECONDS, rpm)
    return rpm


def _effective_provider_rpm(session: Session, channel_id: str) -> int:
    override = _channel_rpm_override(session, channel_id)
    if override is not None and override > 0:
        return override
    return PROXY_PROVIDER_RPM


def _check_model_budget(model: Model, session: Session) -> None:
    """Skip a model before calling upstream when local request budget is full.

    Three layers:
    1. per-model RPM from observed rate-limit headers (or the manual
       whitelist), with PROXY_DEFAULT_MODEL_RPM as a floor for providers
       that never send rate-limit headers (zhipu/xfyun) — without the floor
       the local budget silently never engages and bursts eat live 429s;
    2. per-model RPD (observed/whitelisted only);
    3. per-provider RPM over all models of the channel (PROXY_PROVIDER_RPM).
    """
    limits = _parse_rate_limit_json(model)
    now = _now_utc()
    rpm = limits.get("rpm")
    if not (isinstance(rpm, int) and rpm > 0):
        rpm = PROXY_DEFAULT_MODEL_RPM if PROXY_DEFAULT_MODEL_RPM > 0 else None
    if rpm:
        since = now - timedelta(seconds=60)
        if _passive_call_count(session, model.id, since) >= rpm:
            raise ModelBudgetExceeded(60, "local_rpm_exceeded")

    rpd = limits.get("rpd")
    if isinstance(rpd, int) and rpd > 0:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if _passive_call_count(session, model.id, day_start) >= rpd:
            tomorrow = day_start + timedelta(days=1)
            raise ModelBudgetExceeded(max(1, int((tomorrow - now).total_seconds())), "local_rpd_exceeded")

    provider_rpm = _effective_provider_rpm(session, model.channel_id)
    if provider_rpm > 0:
        since = now - timedelta(seconds=60)
        if _passive_channel_call_count(session, model.channel_id, since) >= provider_rpm:
            raise ModelBudgetExceeded(60, "local_provider_rpm_exceeded")

    tpm_limit = _effective_provider_tpm(session, model.channel_id)
    if tpm_limit and _provider_tpm_used(model.channel_id) >= tpm_limit:
        raise ModelBudgetExceeded(60, "local_provider_tpm_exceeded")


def _upstream_headers(adapter, key: str) -> dict[str, str]:
    """Build proxy headers without assuming every provider needs a key."""
    return {
        **adapter.request_headers(key),
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/iamfuzi/available-computing",
    }


def _build_openai_payload(body: ChatRequest):
    payload = {
        "model": body.model,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
        "stream": body.stream or False,
    }
    for field in ("max_tokens", "temperature", "top_p", "stop", "frequency_penalty", "presence_penalty"):
        val = getattr(body, field, None)
        if val is not None:
            payload[field] = val
    return payload


def _parse_chat_completion_payload(response: httpx.Response) -> dict | None:
    """Return a minimally valid Chat Completions payload, else ``None``.

    Free upstreams occasionally answer HTTP 200 with HTML, an empty object, or
    another non-OpenAI shape. Treating that as success poisons health scoring
    and pushes a parsing failure onto the caller. Keep validation deliberately
    small: callers may legitimately receive tool calls or a null ``content``.
    """
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict) or not isinstance(first_choice.get("message"), dict):
        return None
    return payload


async def _proxy_stream(
    response: httpx.Response,
    client: httpx.AsyncClient,
    model_id: str,
    channel_id: str,
    key: str,
    slot_key: str | None = None,
):
    """Forward SSE chunks and record health when done.

    Rate limits that fire *after* the 200/headers were sent come back as SSE
    events (``{"error": {...}}`` or ``finish_reason: "error"``), never as an
    HTTP 429 — OpenRouter documents this for its free tier. Detecting them
    here keeps the failure out of the success-based health stats and applies
    the normal rate-limit cooldown.
    """
    start = time.monotonic()
    error_code = None
    rate_limited_mid_stream = False
    sse_bytes = 0
    try:
        async for line in response.aiter_lines():
            yield line + "\n\n"
            if line.startswith("data:"):
                sse_bytes += len(line)
            if line.startswith("data:") and '"error"' in line:
                try:
                    chunk = json.loads(line[5:].strip())
                except ValueError:
                    chunk = None
                if isinstance(chunk, dict):
                    err = chunk.get("error")
                    finish_reason = None
                    choices = chunk.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        finish_reason = choices[0].get("finish_reason")
                    if err:
                        code = err.get("code") if isinstance(err, dict) else None
                        if code == 429:
                            rate_limited_mid_stream = True
                        else:
                            error_code = "upstream_error"
                    elif finish_reason == "error":
                        error_code = "upstream_error"
            if line.startswith("data: [DONE]"):
                break
    except Exception:
        error_code = "network_error"
    finally:
        ms = int((time.monotonic() - start) * 1000)
        # Streams carry no usage object — estimate tokens from SSE bytes so the
        # channel TPM window still sees streaming traffic.
        _record_provider_tokens(channel_id, sse_bytes // _PROVIDER_TPM_SSE_BYTES_PER_TOKEN)
        if rate_limited_mid_stream:
            with Session(engine) as session:
                record_rate_limit(model_id, None, session, response_ms=ms)
        else:
            await record_passive_health(model_id, ms, error_code, channel_id, key)
        await client.aclose()
        _release_model_slot(slot_key)


def _diagnostic_headers(
    *,
    route: str | None = None,
    selected_model: str | None = None,
    selected_provider: str | None = None,
    attempted_models: list[str] | None = None,
    retry_after: int | None = None,
    selected_verified_at: datetime | None = None,
    fallback_triggered: bool | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    if route:
        headers["X-AC-Route"] = route
    if selected_model:
        headers["X-AC-Selected-Model"] = selected_model
    if selected_provider:
        headers["X-AC-Selected-Provider"] = selected_provider
    if selected_model and selected_provider:
        headers["X-AC-Actual-Model"] = f"{selected_provider}/{selected_model}"
    if selected_model:
        if fallback_triggered is None:
            fallback_triggered = bool(attempted_models and len(attempted_models) > 1)
        headers["X-AC-Fallback-Triggered"] = str(fallback_triggered).lower()
    if selected_verified_at:
        headers["X-AC-Model-Verified-At"] = selected_verified_at.isoformat()
    if attempted_models is not None:
        headers["X-AC-Attempted-Models"] = ",".join(attempted_models)
        headers["X-AC-Attempt-Count"] = str(len(attempted_models))
        headers["X-AC-Fallback-Count"] = str(max(0, len(attempted_models) - 1))
    if retry_after is not None:
        headers["X-AC-Retry-After"] = str(retry_after)
        # RFC 7231 standard so generic HTTP clients honor the backoff without
        # AC-specific header knowledge.
        headers["Retry-After"] = str(retry_after)
    return headers


def _attach_diagnostic_headers(response, **kwargs):
    for key, value in _diagnostic_headers(**kwargs).items():
        response.headers[key] = value
    return response


def _is_channel_billing_failure(channel: Channel, status_code: int, response_text: str) -> bool:
    if channel.provider_type == "siliconflow" and status_code == 403:
        lowered = response_text.lower()
        return "balance is insufficient" in lowered or '"code":30001' in lowered or '"code": 30001' in lowered
    return False


def _log_proxy_request(
    *,
    category: str | None,
    request_id: str | None,
    requested_model: str | None,
    outcome: str,
    status_code: int | None = None,
    error_code: str | None = None,
    selected_model: str | None = None,
    provider: str | None = None,
    latency_ms: int | None = None,
    attempted: list[str] | None = None,
    api_key_id: str | None = None,
) -> None:
    """Persist one terminal request row (7-day retention). Diagnostics must
    never break proxying, so failures are swallowed with a log line."""
    try:
        from models import RequestLog
        with Session(engine) as log_session:
            log_session.add(RequestLog(
                request_id=request_id, api_key_id=api_key_id, category=category or "",
                requested_model=requested_model, selected_model=selected_model,
                provider=provider, outcome=outcome, status_code=status_code,
                error_code=error_code, latency_ms=latency_ms,
                attempted=",".join(attempted) if attempted else None,
            ))
            log_session.commit()
    except Exception:
        logger.exception("request log write failed")


def _make_ac_error(
    status_code: int,
    message: str,
    error_type: str,
    code: str,
    *,
    param: str | None = None,
    retry_after: int | None = None,
    attempted_models: list[str] | None = None,
    route: str | None = None,
    request_id: str | None = None,
    scope: str | None = None,
):
    """Build a standardized AC error response.

    Thin wrapper over :func:`services.errors.make_ac_error` that adds the
    ``retryable``/``scope``/``request_id`` fields and the standard
    ``Retry-After`` header. Existing call sites pass the legacy positional
    args; HTTP entrypoints additionally pass ``request_id`` from the request.
    """
    # Every error response funnels through here — one place to make failures
    # survivable in the request log after docker logs are gone.
    _log_proxy_request(
        category=None,
        request_id=request_id,
        requested_model=route,
        outcome="fail",
        status_code=status_code,
        error_code=code,
        attempted=attempted_models,
    )
    return errors.make_ac_error(
        status_code,
        message,
        error_type,
        code,
        param=param,
        retry_after=retry_after,
        attempted_models=attempted_models,
        route=route,
        request_id=request_id,
        scope=scope,
    )


def _make_openai_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str = "invalid_request",
    *,
    request_id: str | None = None,
):
    return _make_ac_error(status_code, message, error_type, code, param=param, request_id=request_id)


def _resolve_profile(auth, body, request_id: str):
    """Resolve and authorize the routing profile named in the request body.

    Returns ``(profile, None)`` on success, or ``(None, JSONResponse)`` with a
    ``policy_rejected`` error when the profile is missing, unknown, or the
    caller's ApiKey is not authorized for it. ``profile`` is None (no error)
    when the request does not name a profile at all.
    """
    profile_name = getattr(getattr(body, "routing_policy", None), "profile", None)
    if not profile_name:
        return None, None
    profile = _load_profile(profile_name)
    if profile is None:
        return None, _make_ac_error(
            404,
            f"Routing profile '{profile_name}' does not exist",
            "policy_rejected",
            "profile_not_found",
            param="routing_policy.profile",
            request_id=request_id,
            scope="routing_profile",
        )
    if not _is_profile_authorized(auth, profile_name):
        return None, _make_ac_error(
            403,
            f"API key is not authorized to use routing profile '{profile_name}'",
            "policy_rejected",
            "profile_unauthorized",
            param="routing_policy.profile",
            request_id=request_id,
            scope="routing_profile",
        )
    return profile, None


def _ac_model_info(model: Model, channel: Channel | None, session: Session) -> dict:
    cooling = _is_cooling_down(model)
    status = "rate_limited" if cooling else model.health_status
    return {
        "id": model.model_id,
        "model_id": model.model_id,
        "provider_type": channel.provider_type if channel else None,
        "provider_name": channel.name if channel else None,
        "category": model.category,
        "health_status": status,
        "route_eligible": _model_route_eligible(model, session),
        "is_free": model.is_free,
        "free_type": model.free_type,
        "free_source": model.free_source,
        "last_response_ms": model.last_response_ms,
        "last_checked_at": model.last_checked_at,
        "last_success_at": model.last_success_at,
        "last_verified_at": model.last_verified_at,
        "verification_method": model.verification_method,
        "staleness_threshold_days": model.staleness_threshold_days,
        "rate_limited_until": model.rate_limited_until,
        "channel_status": channel.status if channel else None,
        "last_429_at": model.last_429_at,
        "consecutive_429": model.consecutive_429,
        "param_size": model.param_size,
        "context_length": model.context_length,
    }


@router.get("/ac/models")
def ac_models(
    category: Optional[str] = None,
    include_unavailable: bool = True,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """Available Computing model diagnostics for third-party clients."""
    stmt = select(Model).where(Model.is_active == True).where(Model.is_free == True)
    if category:
        stmt = stmt.where(Model.category == category)
    models = session.exec(stmt).all()
    models = _apply_routing_policy(models, _effective_routing_policy(auth), session)
    channels = {ch.id: ch for ch in session.exec(select(Channel)).all()}

    rows = [_ac_model_info(m, channels.get(m.channel_id), session) for m in models]
    if not include_unavailable:
        rows = [r for r in rows if r["route_eligible"]]
    rows.sort(key=lambda r: (not r["route_eligible"], r["last_response_ms"] is None, r["last_response_ms"] or 999999, r["model_id"]))
    return {"object": "list", "data": rows}


@router.get("/ac/status")
def ac_status(
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """Machine-readable pool and route status for third-party integrations."""
    models = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
    ).all()
    policy = _effective_routing_policy(auth)
    models = _apply_routing_policy(models, policy, session)

    distribution = {"available": 0, "rate_limited": 0, "degraded": 0, "unverified": 0, "unavailable": 0}
    for m in models:
        if _model_route_eligible(m, session):
            distribution["available"] += 1
        elif _is_cooling_down(m) or m.health_status == "rate_limited":
            distribution["rate_limited"] += 1
        elif m.health_status == "slow":
            distribution["degraded"] += 1
        elif m.health_status == "unknown":
            distribution["unverified"] += 1
        else:
            distribution["unavailable"] += 1

    def route_info(route: str, category: str | None = None) -> dict:
        if route == "auto:smart":
            candidates = _apply_routing_policy(
                _auto_candidate_models("smart", session), policy, session, preserve_smart_order=True
            )
        elif route == "auto:fast":
            candidates = _apply_routing_policy(_auto_candidate_models("fast", session), policy, session)
        else:
            candidates = _apply_routing_policy(
                _auto_candidate_models(category or "text", session), policy, session
            )
        return {
            "available": len(candidates) > 0,
            "candidate_count": len(candidates),
            "recommended": route in {"auto:text", "auto:fast"},
            "selected_model": candidates[0].model_id if candidates else None,
        }

    return {
        "object": "available_computing.status",
        "available_model_count": distribution["available"],
        "free_model_count": len(models),
        "distribution": distribution,
        "routes": {
            "auto:text": route_info("auto:text", "text"),
            "auto:vision": route_info("auto:vision", "vision"),
            "auto:code": route_info("auto:code", "code"),
            "auto:fast": route_info("auto:fast"),
            "auto:smart": route_info("auto:smart"),
        },
    }


@router.post("/ac/self-test")
def ac_self_test(
    request: Request,
    body: SelfTestRequest | None = None,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """Non-consuming integration self-test for third-party clients.

    Resolves the routing profile (if any) the same way the chat endpoint
    does, so a caller can verify "this key + this profile actually yields a
    routable candidate" without consuming upstream quota.
    """
    route = (body.model if body else "auto:text")
    request_id = get_request_id(request)

    def _key_limits_payload() -> dict:
        # Third parties have no other place to SEE the limits that apply to
        # their key — surface them here alongside the pre-flight result.
        from models import KeyUsageDay
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today = 0
        if auth is not None:
            rows = session.exec(
                select(KeyUsageDay)
                .where(KeyUsageDay.day == day)
                .where(KeyUsageDay.api_key_id == auth.id)
            ).all()
            today = sum(r.count for r in rows)
        return {
            "key_rpm": getattr(auth, "rate_limit_rpm", None) if auth else None,
            "key_rpd": getattr(auth, "rate_limit_rpd", None) if auth else None,
            "platform_default_rpm": PROXY_API_KEY_RATE_LIMIT,
            "today_requests": today,
            "note": "key_rpm/key_rpd 为空表示仅受平台默认限流；超限返回 429 并附 Retry-After",
        }

    key_limits = _key_limits_payload()
    from services.notices_center import active_notices as _active_notices
    notices = [
        {k: n.get(k) for k in ("id", "ts", "level", "title", "action_required")}
        for n in _active_notices()[:3]
    ]
    profile, profile_error = _resolve_profile(auth, body, request_id)
    if profile_error is not None:
        # _resolve_profile already built a complete JSONResponse; return it as-is.
        return profile_error
    policy = _effective_routing_policy(auth, body.routing_policy if body else None, profile)
    candidates, error = _request_candidate_models(route, session, policy)
    if error:
        return {
            "ok": False,
            "route": route,
            "code": "no_available_models" if _AUTO_RE.match(route) else "model_not_found",
            "message": error,
            "selected_model": None,
            "candidate_count": 0,
            "key_limits": key_limits,
            "notices": notices,
        }

    checked: list[dict] = []
    for model in candidates[:_MAX_UPSTREAM_ATTEMPTS]:
        binding = _try_bind_model(model, session)
        if not binding:
            checked.append({"model": model.model_id, "ok": False, "reason": "channel_unavailable"})
            continue
        try:
            _check_model_budget(model, session)
        except ModelBudgetExceeded as exc:
            checked.append({"model": model.model_id, "ok": False, "reason": exc.reason, "retry_after": exc.retry_after})
            continue
        if _is_cooling_down(model):
            checked.append({"model": model.model_id, "ok": False, "reason": "rate_limited"})
            continue
        checked.append({"model": model.model_id, "ok": True, "reason": None})
        return {
            "ok": True,
            "route": route,
            "selected_model": model.model_id,
            "candidate_count": len(candidates),
            "checked": checked,
            "key_limits": key_limits,
            "notices": notices,
        }

    return {
        "ok": False,
        "route": route,
        "code": "no_routeable_candidates",
        "message": "Candidates exist, but none can be routed right now",
        "selected_model": None,
        "candidate_count": len(candidates),
        "checked": checked,
        "key_limits": key_limits,
        "notices": notices,
    }


@router.get("/models")
def list_openai_models(
    category: Optional[str] = None,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """OpenAI-compatible model listing.

    Returns active, free, non-down models. Each entry carries a `param_size`
    field (parameter count in billions) used by the `auto:smart` router; it is
    null for models whose size couldn't be determined.

    By default only chat-eligible models are returned (backward compatible).
    Pass a `category` query param to scope to a non-chat pool:

      • category=embedding — embedding models (callable via /v1/embeddings)
      • category=rerank    — rerank models (callable via /v1/rerank)
      • category=all       — every category, including non-chat
    """
    models = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status.in_(["healthy", "slow"]))
    ).all()
    models = _apply_routing_policy(models, _effective_routing_policy(auth), session)
    channels = {ch.id: ch for ch in session.exec(select(Channel)).all()}

    # The same upstream :free model can exist on several channels (OpenRouter
    # and Kilo expose identical slugs). Callers get one entry per model_id —
    # the best routable copy (routing order; a cooled-down or ineligible copy
    # yields to its twin instead of suppressing the entry).
    data = []
    listed_ids: set[str] = set()
    for m in sorted(models, key=lambda x: _route_score_key(x, session)):
        if m.model_id in listed_ids:
            continue
        if _is_cooling_down(m):
            continue
        if not _channel_route_eligible(channels.get(m.channel_id)):
            continue
        if category == "all":
            pass
        elif category:
            if (m.category or "text") != category:
                continue
        else:
            if not _is_pool_eligible(m, session):
                continue
        listed_ids.add(m.model_id)
        data.append({
            "id": m.model_id,
            "object": "model",
            "created": 0,
            "owned_by": "available-computing",
            "param_size": m.param_size,
            "x_ac_metadata": {
                "context_length": m.context_length,
                "health_status": m.health_status,
                "health_score": round(_recent_success_rate(m, session), 3),
                "latency_p50_ms": m.last_response_ms,
                "last_verified_at": m.last_verified_at,
                "verification_method": m.verification_method,
                "staleness_threshold_days": m.staleness_threshold_days,
                "free_type": m.free_type,
                "modalities": ["text", "image"] if m.category == "vision" else [m.category or "text"],
            },
        })
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatRequest,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """OpenAI-compatible chat completion.

    The `model` field accepts either a concrete id or an auto-routing prefix:
    `auto:smart` (largest model), `auto:fast` (fastest model), or
    `auto:<category>` (text/vision/code). See the `model` field schema for
    details.
    """
    ip = request.client.host if request.client else "unknown"
    request_id = get_request_id(request)
    from services.usage import record_usage

    _usage_key_id = auth.id if auth is not None else "admin"
    try:
        _check_proxy_rate_limit(
            ip,
            body.model,
            request.headers.get("Authorization"),
            auth,
        )
    except ProxyRateLimitExceeded as exc:
        record_usage(_usage_key_id, "chat", "rejected_local")
        return _make_ac_error(
            429,
            "Local proxy rate limit exceeded",
            "rate_limit_error",
            "local_rate_limited",
            retry_after=exc.retry_after,
            route=body.model,
            request_id=request_id,
        )

    profile, profile_error = _resolve_profile(auth, body, request_id)
    if profile_error is not None:
        return profile_error
    policy = _effective_routing_policy(auth, body.routing_policy, profile)
    candidate_models, error = _request_candidate_models(body.model, session, policy)
    if error:
        if _AUTO_RE.match(body.model):
            unavailable_kind, retry_after = _classify_auto_route_unavailability(
                body.model, session, policy
            )
            if unavailable_kind == "rate_limited":
                return _make_ac_error(
                    429,
                    "All eligible models are currently rate limited",
                    "rate_limited",
                    "all_candidates_rate_limited",
                    retry_after=retry_after,
                    attempted_models=[],
                    route=body.model,
                    request_id=request_id,
                )
            if unavailable_kind == "temporarily_unavailable":
                return _make_ac_error(
                    503,
                    "Eligible models exist but are temporarily unavailable",
                    "routing_exhausted",
                    "all_candidates_unavailable",
                    attempted_models=[],
                    route=body.model,
                    request_id=request_id,
                )
            return _make_ac_error(
                404,
                error,
                "no_eligible_model",
                "no_eligible_model",
                param="model",
                route=body.model,
                request_id=request_id,
            )
        return _make_ac_error(
            404,
            error,
            "invalid_request_error",
            "model_not_found",
            param="model",
            route=body.model,
            request_id=request_id,
        )

    attempted: list[str] = []
    primary_candidates, _ = _single_route_candidates(body.model, session, policy)
    primary_candidate_ids = {candidate.id for candidate in primary_candidates}
    logger.info(
        "route resolve request_id=%s route=%s profile=%s candidates=%d stream=%s",
        request_id, body.model, policy.profile_name, len(candidate_models), bool(body.stream),
    )
    # The profile may cap total attempts and per-provider attempts to force
    # cross-provider fan-out. Without a profile, keep the legacy ceiling.
    attempt_ceiling = policy.max_attempts or _MAX_UPSTREAM_ATTEMPTS
    per_provider_ceiling = policy.max_attempts_per_provider
    # Per-try upstream timeout derived from the profile deadline. Split the
    # deadline evenly across the attempt budget so N fallback tries cannot
    # accumulate into a multi-minute hang. Without a deadline the legacy 120s
    # ceiling applies. Floor at 5s so a tight deadline still lets a request
    # complete; cap at 120s to preserve the legacy upper bound.
    if policy.deadline_ms:
        per_try_timeout = max(5.0, min(120.0, policy.deadline_ms / 1000.0 / attempt_ceiling))
    else:
        per_try_timeout = 120.0
    attempts_per_provider: dict[str, int] = {}
    original_model = body.model
    last_rate_retry_after: int | None = None
    last_upstream_status: int | None = None
    last_failure_kind: str | None = None
    busy_models: list[str] = []
    budget_limited: list[str] = []
    budget_retry_after: int | None = None
    # (status, error_type, error_code) of the first hard upstream rejection;
    # replayed verbatim if the whole chain rejects the request.
    first_rejection: tuple[int, str, str] | None = None
    failed_channels: set[str] = set()

    # Iterate the full candidate list but stop once we have made
    # ``attempt_ceiling`` real upstream attempts. We cannot simply slice
    # candidate_models[:max_attempts] when a per-provider ceiling is set,
    # because skipped (over-budget / busy / per-provider-capped) candidates
    # must not consume the attempt budget.
    upstream_attempts_made = 0
    for candidate_index, model in enumerate(candidate_models):
        if upstream_attempts_made >= attempt_ceiling:
            break
        qualified = f"{model.model_id}@{model.channel_id}"
        if model.channel_id in failed_channels:
            logger.debug("skip request_id=%s model=%s reason=channel_failed", request_id, qualified)
            continue
        binding = _try_bind_model(model, session)
        if not binding:
            logger.debug("skip request_id=%s model=%s reason=bind_failed", request_id, qualified)
            continue
        channel, adapter, key = binding
        # Per-provider fan-out cap: once a provider has been tried enough,
        # skip its remaining models so the chain moves to another provider.
        if per_provider_ceiling is not None:
            if attempts_per_provider.get(channel.provider_type, 0) >= per_provider_ceiling:
                logger.debug(
                    "skip request_id=%s model=%s reason=per_provider_cap provider=%s cap=%s",
                    request_id, qualified, channel.provider_type, per_provider_ceiling,
                )
                continue
        try:
            _check_model_budget(model, session)
        except ModelBudgetExceeded as exc:
            budget_limited.append(model.model_id)
            budget_retry_after = max(budget_retry_after or 0, exc.retry_after)
            logger.debug(
                "skip request_id=%s model=%s reason=budget retry_after=%s",
                request_id, qualified, exc.retry_after,
            )
            continue
        slot_key, acquired = await _try_acquire_model_slot(channel, model)
        if not acquired:
            busy_models.append(model.model_id)
            logger.debug("skip request_id=%s model=%s reason=busy", request_id, qualified)
            continue
        # Record provider-qualified ids so the attempt trace distinguishes
        # which supplier served a model that exists on multiple channels.
        attempted.append(f"{channel.provider_type}/{model.model_id}")
        upstream_attempts_made += 1
        attempts_per_provider[channel.provider_type] = (
            attempts_per_provider.get(channel.provider_type, 0) + 1
        )
        body.model = model.model_id
        payload = _build_openai_payload(body)

        base_url = channel.base_url or adapter.default_base_url
        url = f"{base_url}/chat/completions"
        headers = _upstream_headers(adapter, key)

        start = time.monotonic()
        if body.stream:
            client = httpx.AsyncClient(timeout=httpx.Timeout(per_try_timeout, connect=10.0))
            req = client.build_request("POST", url, json=payload, headers=headers)
            try:
                response = await client.send(req, stream=True)
            except httpx.HTTPError:
                await client.aclose()
                _release_model_slot(slot_key)
                ms = int((time.monotonic() - start) * 1000)
                await record_passive_health(model.id, ms, "network_error", channel.id, key)
                last_upstream_status = 503
                last_failure_kind = "network"
                continue

            if response.status_code == 200:
                _stream_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "upstream ok request_id=%s provider=%s model=%s status=200 stream=true ms=%s attempt=%d",
                    request_id, channel.provider_type, model.model_id,
                    _stream_ms, upstream_attempts_made,
                )
                record_usage(_usage_key_id, "chat", "success")
                _log_proxy_request(
                    category="chat", request_id=request_id, api_key_id=_usage_key_id,
                    requested_model=original_model, selected_model=model.model_id,
                    provider=channel.provider_type, outcome="success", status_code=200,
                    latency_ms=_stream_ms, attempted=attempted,
                )
                return StreamingResponse(
                    _proxy_stream(response, client, model.id, channel.id, key, slot_key),
                    media_type="text/event-stream",
                    headers={
                        "X-Accel-Buffering": "no",
                        "Cache-Control": "no-cache",
                        **_diagnostic_headers(
                            route=original_model,
                            selected_model=model.model_id,
                            selected_provider=channel.provider_type,
                            attempted_models=attempted,
                            selected_verified_at=model.last_verified_at,
                            fallback_triggered=(
                                model.id not in primary_candidate_ids
                                or len(attempted) > 1
                            ),
                            request_id=request_id,
                        ),
                    },
                )

            error_body = await response.aread()
            await client.aclose()
            _release_model_slot(slot_key)
            ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "upstream fail request_id=%s provider=%s model=%s status=%s ms=%s attempt=%d stream=true",
                request_id, channel.provider_type, model.model_id,
                response.status_code, ms, upstream_attempts_made,
            )
            if response.status_code == 429:
                last_rate_retry_after = record_rate_limit(model.id, _parse_retry_after(response.headers), session, ms)
                trigger_event_recheck(model.id, "rate_limited")
                last_upstream_status = 429
                last_failure_kind = "http"
                continue
            if response.status_code >= 500:
                await record_passive_health(model.id, ms, "server_error", channel.id, key)
            if response.status_code in (401, 402, 403):
                record_billing_failure(model.id, response.status_code, session)
                trigger_event_recheck(model.id, f"upstream_{response.status_code}")
                error_text = error_body.decode(errors="ignore") if isinstance(error_body, bytes) else str(error_body)
                if response.status_code in (401, 403) and _is_channel_billing_failure(channel, response.status_code, error_text):
                    record_channel_billing_failure(channel.id, response.status_code, session)
                    from services.notifications import broadcast_notifications_updated
                    await broadcast_notifications_updated()
                failed_channels.add(channel.id)
            last_upstream_status = response.status_code
            last_failure_kind = "http"
            if response.status_code not in _RETRYABLE_UPSTREAM_STATUSES:
                error_code = (
                    "upstream_auth_failed"
                    if response.status_code in (401, 403)
                    else "upstream_non_retryable_error"
                )
                error_type = (
                    "upstream_invalid_response"
                    if response.status_code in (401, 403)
                    else "invalid_request_error"
                )
                if first_rejection is None:
                    first_rejection = (response.status_code, error_type, error_code)
                continue
            continue

        r = None
        try:
            async with httpx.AsyncClient(timeout=per_try_timeout) as client:
                r = await client.post(url, json=payload, headers=headers)
            ms = int((time.monotonic() - start) * 1000)
        except httpx.HTTPError:
            ms = int((time.monotonic() - start) * 1000)
            await record_passive_health(model.id, ms, "network_error", channel.id, key)
            last_upstream_status = 503
            last_failure_kind = "network"
            continue
        finally:
            _release_model_slot(slot_key)

        if r.status_code == 200:
            response_payload = _parse_chat_completion_payload(r)
            if response_payload is None:
                await record_passive_health(
                    model.id, ms, "invalid_response", channel.id, key
                )
                last_upstream_status = 502
                last_failure_kind = "invalid_response"
                logger.warning(
                    "upstream fail request_id=%s provider=%s model=%s "
                    "status=200 reason=invalid_response ms=%s attempt=%d",
                    request_id,
                    channel.provider_type,
                    model.model_id,
                    ms,
                    upstream_attempts_made,
                )
                continue
            # A 200 whose content is empty is a routing-level failure in
            # disguise: reasoning-style models burn the caller's output cap on
            # thinking and return no answer (z1 + max_tokens=1024, hotspot
            # pipeline 2026-09-30). Record it as a passive failure so the
            # model demotes and auto routes drift away; the response itself
            # is still forwarded verbatim to the caller.
            _choice = (response_payload.get("choices") or [{}])[0]
            _message = _choice.get("message") or {}
            _content = _message.get("content")
            _content_text = _content if isinstance(_content, str) else ""
            _has_reasoning = bool(
                (_message.get("reasoning_content") or _message.get("reasoning") or "").strip()
            )
            _empty_body = (
                not _content_text.strip()
                and (_has_reasoning or _choice.get("finish_reason") == "length")
            )
            if _empty_body and _AUTO_RE.match(original_model or ""):
                # auto:* promises usable content. A 200 whose body was eaten
                # by reasoning is a failed attempt for auto routes — fall
                # through to the next candidate instead of handing the caller
                # a null content. Concrete model ids keep faithful passthrough
                # (the caller chose that model; truncation is their setting).
                await record_passive_health(model.id, ms, "empty_content", channel.id, key)
                record_usage(_usage_key_id, "chat", "fail")
                _log_proxy_request(
                    category="chat", request_id=request_id, api_key_id=_usage_key_id,
                    requested_model=original_model, selected_model=model.model_id,
                    provider=channel.provider_type, outcome="fail", status_code=200,
                    error_code="empty_content", latency_ms=ms, attempted=attempted,
                )
                logger.warning(
                    "upstream empty-content failover request_id=%s provider=%s model=%s ms=%s",
                    request_id, channel.provider_type, model.model_id, ms,
                )
                last_upstream_status = 200
                last_failure_kind = "empty_content"
                continue
            await record_passive_health(
                model.id, ms, "empty_content" if _empty_body else None, channel.id, key
            )
            if not _empty_body:
                clear_billing_failures(model.id, session)
                clear_rate_limit(model.id, session)
            _usage = (response_payload.get("usage") or {}).get("total_tokens")
            _record_provider_tokens(channel.id, _usage if isinstance(_usage, int) else 0)
            logger.info(
                "upstream ok request_id=%s provider=%s model=%s status=200 ms=%s attempt=%d",
                request_id, channel.provider_type, model.model_id, ms, upstream_attempts_made,
            )
            record_usage(_usage_key_id, "chat", "fail" if _empty_body else "success")
            _log_proxy_request(
                category="chat", request_id=request_id, api_key_id=_usage_key_id,
                requested_model=original_model, selected_model=model.model_id,
                provider=channel.provider_type,
                outcome="fail" if _empty_body else "success", status_code=200,
                error_code="empty_content" if _empty_body else None,
                latency_ms=ms, attempted=attempted,
            )
            return JSONResponse(
                content=response_payload,
                status_code=200,
                headers=_diagnostic_headers(
                    route=original_model,
                    selected_model=model.model_id,
                    selected_provider=channel.provider_type,
                    attempted_models=attempted,
                    selected_verified_at=model.last_verified_at,
                    fallback_triggered=(
                        model.id not in primary_candidate_ids
                        or len(attempted) > 1
                    ),
                    request_id=request_id,
                ),
            )
        logger.warning(
            "upstream fail request_id=%s provider=%s model=%s status=%s ms=%s attempt=%d",
            request_id, channel.provider_type, model.model_id,
            r.status_code, ms, upstream_attempts_made,
        )
        if r.status_code == 429:
            last_rate_retry_after = record_rate_limit(model.id, _parse_retry_after(r.headers), session, ms)
            trigger_event_recheck(model.id, "rate_limited")
            last_upstream_status = 429
            last_failure_kind = "http"
            continue
        if r.status_code >= 500:
            await record_passive_health(model.id, ms, "server_error", channel.id, key)
        if r.status_code in (401, 402, 403):
            record_billing_failure(model.id, r.status_code, session)
            trigger_event_recheck(model.id, f"upstream_{r.status_code}")
            if r.status_code in (401, 403) and _is_channel_billing_failure(channel, r.status_code, r.text):
                record_channel_billing_failure(channel.id, r.status_code, session)
                from services.notifications import broadcast_notifications_updated
                await broadcast_notifications_updated()
                failed_channels.add(channel.id)
        last_upstream_status = r.status_code
        last_failure_kind = "http"
        if r.status_code not in _RETRYABLE_UPSTREAM_STATUSES:
            error_code = (
                "upstream_auth_failed"
                if r.status_code in (401, 403)
                else "upstream_non_retryable_error"
            )
            error_type = (
                "upstream_invalid_response"
                if r.status_code in (401, 403)
                else "invalid_request_error"
            )
            if first_rejection is None:
                first_rejection = (r.status_code, error_type, error_code)
            continue
        continue

    body.model = original_model
    record_usage(
        _usage_key_id, "chat", "fail" if attempted else "rejected_local"
    )
    logger.warning(
        "route exhausted request_id=%s route=%s attempted=%s busy=%d budget_limited=%d last_status=%s",
        request_id, original_model, ",".join(attempted) or "(none)",
        len(busy_models), len(budget_limited), last_upstream_status,
    )
    if not attempted and busy_models:
        return _make_ac_error(
            503,
            "All candidate models are currently busy",
            "service_unavailable",
            "all_candidates_busy",
            attempted_models=busy_models,
            route=original_model,
            request_id=request_id,
        )
    if not attempted and budget_limited:
        return _make_ac_error(
            429,
            "All candidate models are locally rate limited before upstream call",
            "rate_limited",
            "local_model_budget_exceeded",
            retry_after=budget_retry_after,
            attempted_models=budget_limited,
            route=original_model,
            request_id=request_id,
        )
    if last_upstream_status == 429:
        return _make_ac_error(
            429,
            "All attempted candidate free models are currently rate limited",
            "rate_limited",
            "all_candidates_rate_limited",
            retry_after=last_rate_retry_after,
            attempted_models=attempted,
            route=original_model,
            request_id=request_id,
        )
    if last_failure_kind == "empty_content":
        return _make_ac_error(
            502,
            "All candidates returned empty content — reasoning consumed the output budget; raise max_tokens",
            "upstream_error",
            "all_candidates_empty_content",
            attempted_models=attempted,
            route=original_model,
            request_id=request_id,
        )
    if first_rejection is not None:
        # Every candidate rejected the request outright (e.g. a max_tokens
        # value no free model supports). Replay the first rejection verbatim
        # so the caller sees the real upstream status instead of a generic
        # routing_exhausted.
        reject_status, reject_type, reject_code = first_rejection
        return _make_ac_error(
            reject_status,
            f"Upstream rejected the request with status {reject_status}",
            reject_type,
            reject_code,
            attempted_models=attempted,
            route=original_model,
            request_id=request_id,
            scope="upstream",
        )
    # Every candidate has been tried without success — the routing policy was
    # satisfied (candidates existed) but none could complete the request. Map
    # the last upstream status to a standard error type/code.
    if last_failure_kind == "invalid_response":
        final_type, error_code = "upstream_invalid_response", "invalid_upstream_response"
    elif last_upstream_status in (401, 403):
        final_type, error_code = "upstream_invalid_response", "upstream_auth_failed"
    elif last_upstream_status and last_upstream_status >= 500:
        final_type, error_code = "routing_exhausted", "upstream_server_error"
    else:
        final_type, error_code = "routing_exhausted", "upstream_error"
    return _make_ac_error(
        last_upstream_status or 503,
        "No verified candidate model could complete the request",
        final_type,
        error_code,
        attempted_models=attempted,
        route=original_model,
        request_id=request_id,
        scope="upstream",
    )


async def _proxy_passthrough(
    model,
    channel,
    adapter,
    key,
    path_suffix: str,
    payload: dict,
    session: Session,
    requested_route: str | None = None,
    attempted_models: list[str] | None = None,
):
    """Forward a non-chat request to ``{base_url}/<path_suffix>`` and return the
    upstream response verbatim. Used by /v1/embeddings and /v1/rerank.

    Mirrors the chat router's health/error bookkeeping (5xx → slow,
    401/403 → billing-failure count, success → passive healthy record).
    ``attempted_models`` lets the category-failover wrapper surface the full
    candidate chain in diagnostics instead of just the winning binding.
    """
    trace = attempted_models if attempted_models is not None else [model.model_id]
    base_url = channel.base_url or adapter.default_base_url
    route = requested_route or model.model_id
    url = f"{base_url}/{path_suffix}"
    headers = _upstream_headers(adapter, key)
    passthrough_timeout_ms = int(PROXY_PASSTHROUGH_TIMEOUT_SECONDS * 1000)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PROXY_PASSTHROUGH_TIMEOUT_SECONDS) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        await record_passive_health(model.id, passthrough_timeout_ms, "timeout", channel.id, key)
        return _make_ac_error(
            504,
            "Upstream request timed out",
            "upstream_error",
            "upstream_timeout",
            attempted_models=trace,
            route=route,
        )
    except httpx.RequestError:
        await record_passive_health(model.id, 0, "network_error", channel.id, key)
        return _make_ac_error(
            503,
            "Upstream network request failed",
            "upstream_error",
            "upstream_network_error",
            attempted_models=trace,
            route=route,
        )
    ms = int((time.monotonic() - start) * 1000)

    if r.status_code == 200:
        await record_passive_health(model.id, ms, None, channel.id, key)
        clear_billing_failures(model.id, session)
        clear_rate_limit(model.id, session)
        _passthrough_category = {
            "embeddings": "embedding", "rerank": "rerank",
        }.get(path_suffix, "image" if "image" in path_suffix else path_suffix)
        _log_proxy_request(
            category=_passthrough_category, request_id=None, api_key_id=None,
            requested_model=route, selected_model=model.model_id,
            provider=channel.provider_type, outcome="success", status_code=200,
            latency_ms=ms, attempted=trace,
        )
        return JSONResponse(
            content=r.json(),
            status_code=200,
            headers=_diagnostic_headers(
                route=route,
                selected_model=model.model_id,
                selected_provider=channel.provider_type,
                attempted_models=trace,
                selected_verified_at=model.last_verified_at,
                fallback_triggered=len(trace) > 1,
            ),
        )
    # See chat router: 429 cools the model down; 401/403 counts toward
    # billing-failure eviction.
    if r.status_code == 429:
        retry_after = record_rate_limit(model.id, _parse_retry_after(r.headers), session, ms)
        trigger_event_recheck(model.id, "rate_limited")
        return _make_ac_error(
            429,
            "Upstream rate limited",
            "rate_limit_error",
            "model_rate_limited",
            retry_after=retry_after,
            attempted_models=trace,
            route=route,
        )
    if r.status_code >= 500:
        await record_passive_health(model.id, ms, "server_error", channel.id, key)
    if r.status_code in (401, 402, 403):
        record_billing_failure(model.id, r.status_code, session)
        trigger_event_recheck(model.id, f"upstream_{r.status_code}")
    code = "upstream_auth_failed" if r.status_code in (401, 403) else "upstream_server_error" if r.status_code >= 500 else "upstream_error"
    return _make_ac_error(
        r.status_code,
        f"Upstream returned {r.status_code}",
        "upstream_error",
        code,
        attempted_models=trace,
        route=route,
    )


def _resolve_category_bindings(
    model_id: str,
    category: str,
    session: Session,
    policy,
    allow_auto: bool = False,
    limit: int = 3,
):
    """Primary binding first, then same-category fallbacks.

    A concrete id previously resolved to exactly one binding, so any upstream
    hiccup on it (5xx, cooldown, saturation) failed the whole request. Keep
    the tolerant primary match, then append other routable candidates of the
    same category (best route score first) for the failover wrapper.
    """
    bindings: list[tuple] = []
    seen: set[str] = set()

    def _add(resolved):
        if resolved and resolved[0] and resolved[0].id not in seen:
            bindings.append(resolved)
            seen.add(resolved[0].id)

    if allow_auto and model_id.startswith("auto:"):
        _add(_resolve_auto_category_model(category, session, policy))
    else:
        _add(_resolve_category_model(model_id, category, session, policy))
        if not bindings:
            # A concrete id with no match in this category is a caller bug
            # (e.g. a chat model on /v1/embeddings) — surface 404 rather than
            # silently serving a different model. Fallbacks only kick in
            # after the requested model itself resolved but failed upstream.
            return []

    for model in sorted(
        _category_candidates(session, category, policy),
        key=lambda m: _route_score_key(m, session),
    ):
        if len(bindings) >= max(1, limit):
            break
        if model.id in seen:
            continue
        bound = _try_bind_model(model, session)
        if bound:
            bindings.append((model, *bound))
            seen.add(model.id)
    return bindings


async def _passthrough_with_failover(
    *,
    category: str,
    model_id: str,
    path_suffix: str,
    build_payload,
    session: Session,
    policy,
    route: str,
    allow_auto: bool = False,
    auth=None,
):
    """Slot-acquire + passthrough with same-category candidate failover.

    Shared by /v1/embeddings, /v1/rerank and /v1/images/generations: resolve
    up to 3 bindings, walk them until one returns 200, and fall back to local
    rejections (busy / over-budget) when nothing was attempted upstream.
    """
    from services.usage import record_usage

    key_id = auth.id if auth is not None else "admin"

    bindings = _resolve_category_bindings(
        model_id, category, session, policy, allow_auto=allow_auto
    )
    if not bindings:
        record_usage(key_id, category, "rejected_local")
        return _make_ac_error(
            404,
            f"No available {category} model matching '{model_id}'",
            "invalid_request_error",
            "model_not_found",
            param="model",
            route=route,
        )

    attempted: list[str] = []
    busy_models: list[str] = []
    budget_retry_after = 0
    first_error = None
    for model, channel, adapter, key in bindings:
        try:
            _check_model_budget(model, session)
        except ModelBudgetExceeded as exc:
            budget_retry_after = max(budget_retry_after, exc.retry_after)
            continue
        slot_key, acquired = await _try_acquire_model_slot(
            channel, model, category=category
        )
        if not acquired:
            busy_models.append(model.model_id)
            continue
        attempted.append(f"{channel.provider_type}/{model.model_id}")
        try:
            result = await _proxy_passthrough(
                model,
                channel,
                adapter,
                key,
                path_suffix,
                build_payload(model),
                session,
                requested_route=route,
                attempted_models=list(attempted),
            )
        finally:
            _release_model_slot(slot_key)
        if isinstance(result, JSONResponse) and result.status_code == 200:
            record_usage(key_id, category, "success")
            return result
        if first_error is None:
            first_error = result

    if attempted:
        record_usage(key_id, category, "fail")
        return first_error
    record_usage(key_id, category, "rejected_local")
    if busy_models:
        return _make_ac_error(
            503,
            "All candidate models are currently busy",
            "service_unavailable",
            "all_candidates_busy",
            attempted_models=busy_models,
            route=route,
        )
    return _make_ac_error(
        429,
        "All candidate models are locally rate limited before upstream call",
        "rate_limit_error",
        "local_model_budget_exceeded",
        retry_after=budget_retry_after or None,
        attempted_models=[binding[0].model_id for binding in bindings],
        route=route,
    )


def _build_simple_payload(body, *, include: list[str]):
    """Build a forwarding payload from a request body, keeping only ``model`` and
    the listed optional fields when present (non-null)."""
    payload = {"model": body.model}
    for field in include:
        val = getattr(body, field, None)
        if val is not None:
            payload[field] = val
    return payload


@router.post("/images/generations")
async def image_generations(
    request: Request,
    body: ImageGenerationRequest,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """Generate one image through an available free image model.

    The request and response follow OpenAI's URL response shape. ``auto:image``
    selects the best currently verified image model; a concrete image model id
    may also be supplied.
    """
    ip = request.client.host if request.client else "unknown"
    try:
        _check_proxy_rate_limit(
            ip,
            f"images:{body.model}",
            request.headers.get("Authorization"),
            auth,
        )
    except ProxyRateLimitExceeded as exc:
        return _make_ac_error(
            429,
            "Local proxy rate limit exceeded",
            "rate_limit_error",
            "local_rate_limited",
            retry_after=exc.retry_after,
            route=body.model,
        )

    request_id = get_request_id(request)
    profile, profile_error = _resolve_profile(auth, body, request_id)
    if profile_error is not None:
        return profile_error
    policy = _effective_routing_policy(auth, body.routing_policy, profile)

    def _image_payload(model):
        payload = {"model": model.model_id, "prompt": body.prompt}
        for field in ("quality", "size", "watermark_enabled"):
            value = getattr(body, field)
            if value is not None:
                payload[field] = value
        if body.user is not None:
            payload["user_id"] = body.user
        return payload

    return await _passthrough_with_failover(
        category="image",
        model_id=body.model,
        path_suffix="images/generations",
        build_payload=_image_payload,
        session=session,
        policy=policy,
        route=body.model,
        allow_auto=True,
        auth=auth,
    )


@router.post("/embeddings")
async def embeddings(
    request: Request,
    body: EmbeddingRequest,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """OpenAI-compatible embeddings.

    Resolves ``model`` against the embedding candidate pool (concrete id only,
    no auto-routing) and forwards to the upstream ``/embeddings`` endpoint,
    falling back to other embedding candidates on upstream failure.
    """
    ip = request.client.host if request.client else "unknown"
    try:
        _check_proxy_rate_limit(
            ip,
            f"embeddings:{body.model}",
            request.headers.get("Authorization"),
            auth,
        )
    except ProxyRateLimitExceeded as exc:
        return _make_ac_error(
            429,
            "Local proxy rate limit exceeded",
            "rate_limit_error",
            "local_rate_limited",
            retry_after=exc.retry_after,
            route=body.model,
        )

    def _embedding_payload(model):
        payload = _build_simple_payload(
            body, include=["input", "encoding_format"]
        )
        payload["model"] = model.model_id
        return payload

    return await _passthrough_with_failover(
        category="embedding",
        model_id=body.model,
        path_suffix="embeddings",
        build_payload=_embedding_payload,
        session=session,
        policy=_effective_routing_policy(auth),
        route=body.model,
        auth=auth,
    )


@router.post("/rerank")
async def rerank(
    request: Request,
    body: RerankRequest,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """Rerank documents by relevance to a query (SiliconFlow-compatible).

    NOTE: ``/rerank`` is NOT an OpenAI standard endpoint — it follows the
    SiliconFlow/Cohere convention. Resolves ``model`` against the rerank
    candidate pool and forwards to the upstream ``/rerank`` endpoint,
    falling back to other rerank candidates on upstream failure.
    """
    ip = request.client.host if request.client else "unknown"
    try:
        _check_proxy_rate_limit(
            ip,
            f"rerank:{body.model}",
            request.headers.get("Authorization"),
            auth,
        )
    except ProxyRateLimitExceeded as exc:
        return _make_ac_error(
            429,
            "Local proxy rate limit exceeded",
            "rate_limit_error",
            "local_rate_limited",
            retry_after=exc.retry_after,
            route=body.model,
        )

    def _rerank_payload(model):
        payload = _build_simple_payload(
            body, include=["query", "documents", "top_n", "return_documents"]
        )
        payload["model"] = model.model_id
        return payload

    return await _passthrough_with_failover(
        category="rerank",
        model_id=body.model,
        path_suffix="rerank",
        build_payload=_rerank_payload,
        session=session,
        policy=_effective_routing_policy(auth),
        route=body.model,
        auth=auth,
    )
