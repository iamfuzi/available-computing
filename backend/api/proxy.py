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

from database import get_session
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
)
from services.router.scoring import (
    is_cooling_down as _is_cooling_down,
    is_pool_eligible as _is_pool_eligible,
    recent_success_rate as _recent_success_rate,
)
from config import (
    PROXY_RATE_WINDOW_SECONDS,
    PROXY_API_KEY_RATE_LIMIT,
    PROXY_ADMIN_RATE_LIMIT,
    PROXY_IP_FALLBACK_RATE_LIMIT,
    PROXY_MODEL_CONCURRENCY_LIMIT,
)

router = APIRouter()

logger = logging.getLogger(__name__)

# Maximum upstream attempts within a single request's fallback chain. Kept in
# the proxy module (not the router package) because it bounds the HTTP loop.
_MAX_UPSTREAM_ATTEMPTS = 50

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


def _model_slot_key(channel: Channel, model: Model) -> str:
    return f"{channel.provider_type}:{channel.id}:{model.model_id}"


async def _try_acquire_model_slot(channel: Channel, model: Model) -> tuple[str, bool]:
    key = _model_slot_key(channel, model)
    sem = _model_semaphores.setdefault(key, asyncio.Semaphore(PROXY_MODEL_CONCURRENCY_LIMIT))
    if getattr(sem, "_value", 0) <= 0:
        return key, False
    await sem.acquire()
    return key, True


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


def _check_model_budget(model: Model, session: Session) -> None:
    """Skip a model before calling upstream when local request budget is full."""
    limits = _parse_rate_limit_json(model)
    now = _now_utc()
    rpm = limits.get("rpm")
    if isinstance(rpm, int) and rpm > 0:
        since = now - timedelta(seconds=60)
        if _passive_call_count(session, model.id, since) >= rpm:
            raise ModelBudgetExceeded(60, "local_rpm_exceeded")

    rpd = limits.get("rpd")
    if isinstance(rpd, int) and rpd > 0:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if _passive_call_count(session, model.id, day_start) >= rpd:
            tomorrow = day_start + timedelta(days=1)
            raise ModelBudgetExceeded(max(1, int((tomorrow - now).total_seconds())), "local_rpd_exceeded")


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


async def _proxy_stream(
    response: httpx.Response,
    client: httpx.AsyncClient,
    model_id: str,
    channel_id: str,
    key: str,
    slot_key: str | None = None,
):
    """Forward SSE chunks and record health when done."""
    start = time.monotonic()
    error_code = None
    try:
        async for line in response.aiter_lines():
            yield line + "\n\n"
            if line.startswith("data: [DONE]"):
                break
    except Exception:
        error_code = "network_error"
    finally:
        ms = int((time.monotonic() - start) * 1000)
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
        }

    return {
        "ok": False,
        "route": route,
        "code": "no_routeable_candidates",
        "message": "Candidates exist, but none can be routed right now",
        "selected_model": None,
        "candidate_count": len(candidates),
        "checked": checked,
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

    data = []
    for m in models:
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
    try:
        _check_proxy_rate_limit(
            ip,
            body.model,
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
            request_id=request_id,
        )

    profile, profile_error = _resolve_profile(auth, body, request_id)
    if profile_error is not None:
        return profile_error
    policy = _effective_routing_policy(auth, body.routing_policy, profile)
    candidate_models, error = _request_candidate_models(body.model, session, policy)
    if error:
        code = "no_available_models" if _AUTO_RE.match(body.model) else "model_not_found"
        return _make_ac_error(
            404,
            error,
            "invalid_request_error",
            code,
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
    busy_models: list[str] = []
    budget_limited: list[str] = []
    budget_retry_after: int | None = None
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
                continue

            if response.status_code == 200:
                logger.info(
                    "upstream ok request_id=%s provider=%s model=%s status=200 stream=true ms=%s attempt=%d",
                    request_id, channel.provider_type, model.model_id,
                    int((time.monotonic() - start) * 1000), upstream_attempts_made,
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
                                or candidate_index > 0
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
            continue
        finally:
            _release_model_slot(slot_key)

        if r.status_code == 200:
            await record_passive_health(model.id, ms, None, channel.id, key)
            clear_billing_failures(model.id, session)
            clear_rate_limit(model.id, session)
            logger.info(
                "upstream ok request_id=%s provider=%s model=%s status=200 ms=%s attempt=%d",
                request_id, channel.provider_type, model.model_id, ms, upstream_attempts_made,
            )
            return JSONResponse(
                content=r.json(),
                status_code=200,
                headers=_diagnostic_headers(
                    route=original_model,
                    selected_model=model.model_id,
                    selected_provider=channel.provider_type,
                    attempted_models=attempted,
                    selected_verified_at=model.last_verified_at,
                    fallback_triggered=(
                        model.id not in primary_candidate_ids
                        or candidate_index > 0
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
        continue

    body.model = original_model
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
    # Every candidate has been tried without success — the routing policy was
    # satisfied (candidates existed) but none could complete the request. Map
    # the last upstream status to a standard error type/code.
    if last_upstream_status in (401, 403):
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
):
    """Forward a non-chat request to ``{base_url}/<path_suffix>`` and return the
    upstream response verbatim. Used by /v1/embeddings and /v1/rerank.

    Mirrors the chat router's health/error bookkeeping (5xx → slow,
    401/403 → billing-failure count, success → passive healthy record).
    """
    base_url = channel.base_url or adapter.default_base_url
    route = requested_route or model.model_id
    url = f"{base_url}/{path_suffix}"
    headers = _upstream_headers(adapter, key)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        await record_passive_health(model.id, 120000, "timeout", channel.id, key)
        return _make_ac_error(
            504,
            "Upstream request timed out",
            "upstream_error",
            "upstream_timeout",
            attempted_models=[model.model_id],
            route=route,
        )
    except httpx.RequestError:
        await record_passive_health(model.id, 0, "network_error", channel.id, key)
        return _make_ac_error(
            503,
            "Upstream network request failed",
            "upstream_error",
            "upstream_network_error",
            attempted_models=[model.model_id],
            route=route,
        )
    ms = int((time.monotonic() - start) * 1000)

    if r.status_code == 200:
        await record_passive_health(model.id, ms, None, channel.id, key)
        clear_billing_failures(model.id, session)
        clear_rate_limit(model.id, session)
        return JSONResponse(
            content=r.json(),
            status_code=200,
            headers=_diagnostic_headers(
                route=route,
                selected_model=model.model_id,
                selected_provider=channel.provider_type,
                attempted_models=[model.model_id],
                selected_verified_at=model.last_verified_at,
                fallback_triggered=False,
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
            attempted_models=[model.model_id],
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
        attempted_models=[model.model_id],
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
    if body.model == "auto:image":
        resolved = _resolve_auto_category_model("image", session, policy)
    else:
        resolved = _resolve_category_model(body.model, "image", session, policy)
    model, channel, adapter, key = resolved
    if not model:
        return _make_ac_error(
            404,
            f"No available image model matching '{body.model}'",
            "invalid_request_error",
            "model_not_found",
            param="model",
            route=body.model,
        )
    try:
        _check_model_budget(model, session)
    except ModelBudgetExceeded as exc:
        return _make_ac_error(
            429,
            "Model is locally rate limited before upstream call",
            "rate_limit_error",
            "local_model_budget_exceeded",
            retry_after=exc.retry_after,
            attempted_models=[model.model_id],
            route=body.model,
        )
    slot_key, acquired = await _try_acquire_model_slot(channel, model)
    if not acquired:
        return _make_ac_error(
            503,
            "All candidate models are currently busy",
            "service_unavailable",
            "all_candidates_busy",
            attempted_models=[model.model_id],
            route=body.model,
        )

    payload = {"model": model.model_id, "prompt": body.prompt}
    for field in ("quality", "size", "watermark_enabled"):
        value = getattr(body, field)
        if value is not None:
            payload[field] = value
    if body.user is not None:
        payload["user_id"] = body.user
    try:
        return await _proxy_passthrough(
            model,
            channel,
            adapter,
            key,
            "images/generations",
            payload,
            session,
            requested_route=body.model,
        )
    finally:
        _release_model_slot(slot_key)


@router.post("/embeddings")
async def embeddings(
    request: Request,
    body: EmbeddingRequest,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """OpenAI-compatible embeddings.

    Resolves ``model`` against the embedding candidate pool (concrete id only,
    no auto-routing) and forwards to the upstream ``/embeddings`` endpoint.
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
    resolved = _resolve_category_model(
        body.model, "embedding", session, _effective_routing_policy(auth)
    )
    model, channel, adapter, key = resolved
    if not model:
        return _make_ac_error(
            404,
            f"No available embedding model matching '{body.model}'",
            "invalid_request_error",
            "model_not_found",
            param="model",
            route=body.model,
        )
    try:
        _check_model_budget(model, session)
    except ModelBudgetExceeded as exc:
        return _make_ac_error(
            429,
            "Model is locally rate limited before upstream call",
            "rate_limit_error",
            "local_model_budget_exceeded",
            retry_after=exc.retry_after,
            attempted_models=[model.model_id],
            route=body.model,
        )
    slot_key, acquired = await _try_acquire_model_slot(channel, model)
    if not acquired:
        return _make_ac_error(
            503,
            "All candidate models are currently busy",
            "service_unavailable",
            "all_candidates_busy",
            attempted_models=[model.model_id],
            route=body.model,
        )
    payload = _build_simple_payload(body, include=["input", "encoding_format"])
    try:
        return await _proxy_passthrough(model, channel, adapter, key, "embeddings", payload, session)
    finally:
        _release_model_slot(slot_key)


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
    candidate pool and forwards to the upstream ``/rerank`` endpoint.
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
    resolved = _resolve_category_model(
        body.model, "rerank", session, _effective_routing_policy(auth)
    )
    model, channel, adapter, key = resolved
    if not model:
        return _make_ac_error(
            404,
            f"No available rerank model matching '{body.model}'",
            "invalid_request_error",
            "model_not_found",
            param="model",
            route=body.model,
        )
    try:
        _check_model_budget(model, session)
    except ModelBudgetExceeded as exc:
        return _make_ac_error(
            429,
            "Model is locally rate limited before upstream call",
            "rate_limit_error",
            "local_model_budget_exceeded",
            retry_after=exc.retry_after,
            attempted_models=[model.model_id],
            route=body.model,
        )
    slot_key, acquired = await _try_acquire_model_slot(channel, model)
    if not acquired:
        return _make_ac_error(
            503,
            "All candidate models are currently busy",
            "service_unavailable",
            "all_candidates_busy",
            attempted_models=[model.model_id],
            route=body.model,
        )
    payload = _build_simple_payload(body, include=["query", "documents", "top_n", "return_documents"])
    try:
        return await _proxy_passthrough(model, channel, adapter, key, "rerank", payload, session)
    finally:
        _release_model_slot(slot_key)
