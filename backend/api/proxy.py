import re
import json
import time
import httpx
import hashlib
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlmodel import Session, select
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional

from database import get_session
from models import Model, Channel, HealthRecord, ApiKey
from api.auth import verify_token_or_apikey
from api.channels import _decrypt_key
from services.health import (
    record_passive_health,
    record_billing_failure,
    record_channel_billing_failure,
    clear_billing_failures,
    record_rate_limit,
    clear_rate_limit,
)
from services.event_recheck import trigger_event_recheck
from adapters import get_adapter
from config import (
    PROXY_RATE_WINDOW_SECONDS,
    PROXY_API_KEY_RATE_LIMIT,
    PROXY_ADMIN_RATE_LIMIT,
    PROXY_IP_FALLBACK_RATE_LIMIT,
    PROXY_MODEL_CONCURRENCY_LIMIT,
)

router = APIRouter()

_VALID_CATEGORIES = {"text", "vision", "code", "embedding", "image", "video"}
_AUTO_RE = re.compile(r"^auto:(" + "|".join(_VALID_CATEGORIES) + r"|smart|fast)$")

# Routing priority: healthy models are preferred, with slow models as fallback.
# Unknown/down and cooled-down rate-limited models stay out of automatic routing.
_HEALTH_ORDER = {"healthy": 0, "slow": 1}
_MAX_UPSTREAM_ATTEMPTS = 50
_RECENT_SCORE_LIMIT = 20

# Categories that are not chat-completion targets (excluded from model resolution)
_NON_CHAT_CATEGORIES = {"audio", "image", "video", "embedding", "rerank"}

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


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exclude: list[str] = Field(default_factory=list, max_length=50)
    min_context: Optional[int] = Field(default=None, ge=1)
    prefer: Optional[Literal["latency", "capability"]] = None
    fallback_chain: list[str] = Field(default_factory=list, max_length=10)


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


@dataclass(frozen=True)
class EffectiveRoutingPolicy:
    provider_whitelist: frozenset[str]
    provider_blacklist: frozenset[str]
    min_context: int | None
    prefer: str
    fallback_chain: tuple[str, ...]


def _parse_provider_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    return {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()


def _effective_routing_policy(
    api_key: ApiKey | None,
    request_policy: RoutingPolicy | None = None,
) -> EffectiveRoutingPolicy:
    whitelist = _parse_provider_ids(api_key.provider_whitelist) if api_key else set()
    blacklist = _parse_provider_ids(api_key.provider_blacklist) if api_key else set()
    key_min_context = api_key.default_min_context if api_key else None
    request_min_context = request_policy.min_context if request_policy else None
    minimums = [value for value in (key_min_context, request_min_context) if value is not None]
    if request_policy:
        blacklist.update(request_policy.exclude)
    return EffectiveRoutingPolicy(
        provider_whitelist=frozenset(whitelist),
        provider_blacklist=frozenset(blacklist),
        min_context=max(minimums) if minimums else None,
        prefer=(request_policy.prefer if request_policy and request_policy.prefer else None)
        or (api_key.default_prefer if api_key else "latency"),
        fallback_chain=tuple(request_policy.fallback_chain if request_policy else ()),
    )


def _apply_routing_policy(
    candidates: list[Model],
    policy: EffectiveRoutingPolicy,
    session: Session,
    *,
    preserve_smart_order: bool = False,
) -> list[Model]:
    allowed: list[Model] = []
    for model in candidates:
        channel = session.get(Channel, model.channel_id)
        if not channel:
            continue
        provider = channel.provider_type
        if policy.provider_whitelist and provider not in policy.provider_whitelist:
            continue
        if provider in policy.provider_blacklist:
            continue
        if policy.min_context is not None and (
            model.context_length is None or model.context_length < policy.min_context
        ):
            continue
        allowed.append(model)
    if policy.prefer == "capability" and not preserve_smart_order:
        allowed.sort(key=lambda model: _route_score_key(model, session, smart=True))
    return allowed


def _health_sort_key(model: Model) -> tuple:
    """Sort key: (health_priority, response_ms). Lower is better."""
    return (_HEALTH_ORDER.get(model.health_status, 3), model.last_response_ms if model.last_response_ms is not None else 999999)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_cooling_down(model: Model) -> bool:
    if not model.rate_limited_until:
        return False
    until = model.rate_limited_until
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > _now_utc()


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


def _recent_success_rate(model: Model, session: Session) -> float:
    records = session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id == model.id)
        .order_by(HealthRecord.checked_at.desc())
        .limit(_RECENT_SCORE_LIMIT)
    ).all()
    if not records:
        return 1.0 if model.health_status == "healthy" else 0.0
    good = sum(1 for r in records if r.status == "healthy")
    return good / len(records)


def _route_score_key(model: Model, session: Session, smart: bool = False) -> tuple:
    priority = _HEALTH_ORDER.get(model.health_status, 3)
    success_penalty = -_recent_success_rate(model, session)
    ms = model.last_response_ms if model.last_response_ms is not None else 999999
    size = -(model.param_size or 0) if smart else 0
    return (priority, success_penalty, size, ms, model.model_id)


def _channel_route_eligible(channel: Channel | None) -> bool:
    if not channel or not channel.enabled or channel.status != "active":
        return False
    if channel.key_expires_at:
        expires_at = channel.key_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return False
    return True


def _try_bind_model(model: Model, session: Session):
    """Try to bind a model to its channel, adapter, and decrypted key."""
    channel = session.get(Channel, model.channel_id)
    if not _channel_route_eligible(channel):
        return None
    adapter = get_adapter(channel.provider_type)
    key = _decrypt_key(channel.api_key_enc, session)
    return channel, adapter, key


def _upstream_headers(adapter, key: str) -> dict[str, str]:
    """Build proxy headers without assuming every provider needs a key."""
    return {
        **adapter.request_headers(key),
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/iamfuzi/available-computing",
    }


# Suffix tokens that mark a chat-tuning variant, not a different model family.
# Stripping these lets "qwen2.5-72b-instruct" normalize to the same key as a
# bare "qwen2.5-72b" without also collapsing genuinely different ids.
_VARIANT_SUFFIXES = {
    "instruct", "chat", "it", "base", "preview", "latest",
}

# Trailing context-window markers like "-128k" / "-32k" / "-8k" are dropped.
_CONTEXT_RE = re.compile(r"-\d+k$", re.IGNORECASE)


def _normalize_model_id(model_id: str) -> str:
    """Normalize a model id for tolerant comparison.

    - lowercase
    - drop a leading ``<org>/`` prefix (e.g. ``Qwen/Qwen2.5-72B`` -> ``qwen2.5-72b``)
    - drop a trailing ``-<NNN>k`` context marker (e.g. ``-128k``)
    - drop a trailing known variant token (``-instruct``, ``-chat``, ``-it``...)

    Unknown trailing tokens (e.g. ``-turbo``, ``-vision``) are preserved, so a
    typo like ``qwen2.5-72b-turbo`` will NOT match the real ``Qwen2.5-72B``.
    """
    s = model_id.lower()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    s = _CONTEXT_RE.sub("", s)
    parts = s.split("-")
    while len(parts) > 1 and parts[-1] in _VARIANT_SUFFIXES:
        parts.pop()
    return "-".join(parts)


def _is_pool_eligible(model: Model, session: Session) -> bool:
    """Whether a model may appear in the free pool.

    Excludes non-chat categories. Free/paid status is trusted from the model's
    is_free flag, which is set authoritatively during discovery via the
    provider's free-model API (SiliconFlow charging_type=free) or the static
    whitelist as a fallback. We intentionally do NOT hard-exclude "Pro/"-prefixed
    ids here: the authoritative API sometimes marks Pro/ variants as free
    (e.g. promotional free tiers), and overriding that would be wrong.
    """
    if (model.category or "text") in _NON_CHAT_CATEGORIES:
        return False
    return True


def _looks_like_vision_model(model_id: str) -> bool:
    lower = model_id.lower()
    return any(token in lower for token in (
        "vision",
        "ocr",
        "captioner",
        "image-edit",
        "qwen-image",
        "omni",
        "internvl",
        "qwen-vl",
        "glm-4v",
        "glm-4.1v",
        "glm-4.5v",
    ))


def _is_generic_text_candidate(model: Model) -> bool:
    return (model.category or "text") == "text" and not _looks_like_vision_model(model.model_id)


def _chat_candidates(session: Session):
    """All active, free, routeable chat models not in rate-limit cooldown."""
    rows = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status.in_(["healthy", "slow"]))
    ).all()
    return [m for m in rows if _is_pool_eligible(m, session) and not _is_cooling_down(m)]


def _pick_best(candidates: list[Model], session: Session, prefer_short_id: bool = False):
    """Sort by health then latency, return first bindable model + (channel, adapter, key).

    When ``prefer_short_id`` is set (used for tolerant/fuzzy matching tiers), models
    with a shorter id (no org/variant prefix like ``LoRA/`` or ``Pro/``) are preferred
    over equally-healthy prefixed siblings, so a bare ``qwen2.5-7b`` resolves to
    ``Qwen/Qwen2.5-7B-Instruct`` rather than ``LoRA/Qwen/Qwen2.5-7B-Instruct``.
    """
    def sort_key(m: Model):
        priority = _HEALTH_ORDER.get(m.health_status, 3)
        ms = m.last_response_ms if m.last_response_ms is not None else 999999
        if prefer_short_id:
            # health bucket first, then prefer shorter id (no org/variant prefix),
            # then latency. This avoids a bare "qwen2.5-7b" resolving to a
            # slightly-faster "LoRA/..." variant instead of the base model.
            return (priority, len(m.model_id), ms, m.model_id)
        return _route_score_key(m, session)
    candidates.sort(key=sort_key)
    for model in candidates:
        result = _try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]
    return None, None, None, None


def _suggest_models(model_id: str, all_models: list[Model], limit: int = 5) -> list[str]:
    """Return closest available model ids for a friendlier 404 message."""
    needle = _normalize_model_id(model_id)
    scored: list[tuple[int, str]] = []
    for m in all_models:
        norm = _normalize_model_id(m.model_id)
        if norm == needle:
            score = 0
        elif norm.startswith(needle) or needle.startswith(norm):
            score = 1
        elif needle in norm or norm in needle:
            score = 2
        else:
            continue
        scored.append((score, m.model_id))
    scored.sort(key=lambda x: x[0])
    # Deduplicate preserving order
    seen = set()
    out = []
    for _, mid in scored:
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
        if len(out) >= limit:
            break
    return out


def _resolve_from_candidates(model_id: str, candidates: list[Model], session: Session):
    """Find a model within ``candidates`` via tolerant three-tier matching.

    Shared by the chat router and the embedding/rerank routers so they all get
    the same fuzzy-matching behaviour (exact → case-insensitive → normalized).

    Returns (model, channel, adapter, key) or (None, None, None, None).
    """
    # Tier 1: exact
    tier1 = [m for m in candidates if m.model_id == model_id]
    found = _pick_best(tier1, session)
    if found[0]:
        return found

    # Tier 2: case-insensitive (also tolerates org-prefix difference)
    lowered = model_id.lower()
    tier2 = [m for m in candidates if m.model_id.lower() == lowered]
    found = _pick_best(tier2, session, prefer_short_id=True)
    if found[0]:
        return found

    # Tier 3: normalized core (drops org prefix + variant suffix).
    # Always run when normalized yields a real core; it may match even when
    # the input is already minimal (e.g. "qwen2.5-72b" -> "qwen2.5-72b").
    norm = _normalize_model_id(model_id)
    if norm:
        tier3 = [m for m in candidates if _normalize_model_id(m.model_id) == norm]
        found = _pick_best(tier3, session, prefer_short_id=True)
        if found[0]:
            return found

    return None, None, None, None


def _matching_models(model_id: str, candidates: list[Model], session: Session, prefer_short_id: bool = False) -> list[Model]:
    """Return matching candidates sorted like _pick_best, without binding."""
    tier1 = [m for m in candidates if m.model_id == model_id]
    if tier1:
        tier1.sort(key=lambda m: _route_score_key(m, session))
        return tier1

    lowered = model_id.lower()
    tier2 = [m for m in candidates if m.model_id.lower() == lowered]
    if tier2:
        tier2.sort(key=lambda m: (_HEALTH_ORDER.get(m.health_status, 3), len(m.model_id), _route_score_key(m, session)))
        return tier2

    norm = _normalize_model_id(model_id)
    if norm:
        tier3 = [m for m in candidates if _normalize_model_id(m.model_id) == norm]
        if tier3:
            tier3.sort(key=lambda m: (_HEALTH_ORDER.get(m.health_status, 3), len(m.model_id), _route_score_key(m, session)))
            return tier3

    return []


def _resolve_model(model_id: str, session: Session):
    """Find an active free healthy chat model and its channel.

    Matching is tolerant, in three tiers:
      1. exact ``model_id`` match
      2. case-insensitive match
      3. normalized match (lowercased, org-prefix dropped, variant suffix stripped)

    Returns (model, channel, adapter, key) or (None, None, None, None).
    """
    return _resolve_from_candidates(model_id, _chat_candidates(session), session)


def _category_candidates(
    session: Session,
    category: str,
    policy: EffectiveRoutingPolicy | None = None,
):
    """All active, free, routable models of a category (e.g. image, embedding).

    Unlike ``_chat_candidates`` this does NOT exclude the non-chat categories —
    it scopes to exactly one. Successful generation probes commonly exceed the
    fast-response threshold, so both healthy and slow models remain routable.
    """
    rows = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status.in_(["healthy", "slow"]))
        .where(Model.category == category)
    ).all()
    candidates = [m for m in rows if not _is_cooling_down(m)]
    if policy:
        candidates = _apply_routing_policy(candidates, policy, session)
    return candidates


def _resolve_category_model(
    model_id: str,
    category: str,
    session: Session,
    policy: EffectiveRoutingPolicy | None = None,
):
    """Resolve a model within a single category (embedding/rerank) using the
    same tolerant matching as the chat router."""
    return _resolve_from_candidates(
        model_id,
        _category_candidates(session, category, policy),
        session,
    )


def _resolve_auto_category_model(
    category: str,
    session: Session,
    policy: EffectiveRoutingPolicy | None = None,
):
    """Select and bind the best routable model in a non-chat category."""
    candidates = _category_candidates(session, category, policy)
    candidates.sort(key=lambda model: _route_score_key(model, session))
    for model in candidates:
        bound = _try_bind_model(model, session)
        if bound:
            return model, bound[0], bound[1], bound[2]
    return None, None, None, None


def _resolve_auto_model(category: str, session: Session):
    """Auto-select the best available model for a given category."""
    candidates = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status == "healthy")
        .where(Model.category == category)
    ).all()
    candidates = [m for m in candidates if not _is_cooling_down(m)]

    candidates.sort(key=_health_sort_key)

    for model in candidates:
        result = _try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]

    return None, None, None, None


def _auto_candidate_models(kind: str, session: Session) -> list[Model]:
    chat_candidates = _chat_candidates(session)
    text_candidates = [m for m in chat_candidates if _is_generic_text_candidate(m)]
    generic_candidates = text_candidates or chat_candidates
    if kind == "smart":
        # auto:smart = "give me the most capable model". Keep deterministic
        # param_size ordering within each tier — the user explicitly asked for
        # the biggest, so randomizing would violate that intent.
        candidates = generic_candidates
        candidates.sort(key=lambda m: _route_score_key(m, session, smart=True))
        return candidates
    if kind == "fast":
        candidates = generic_candidates
        candidates.sort(key=lambda m: _route_score_key(m, session))
        return candidates
    candidates = [m for m in chat_candidates if (m.category or "text") == kind]
    candidates.sort(key=lambda m: _route_score_key(m, session))
    return candidates


def _single_route_candidates(
    model_id: str,
    session: Session,
    policy: EffectiveRoutingPolicy,
) -> tuple[list[Model], str | None]:
    auto_match = _AUTO_RE.match(model_id)
    if auto_match:
        kind = auto_match.group(1)
        candidates = _auto_candidate_models(kind, session)
        candidates = _apply_routing_policy(
            candidates,
            policy,
            session,
            preserve_smart_order=kind == "smart",
        )
        if not candidates:
            return [], f"No verified available models for {model_id}"
        return candidates, None

    pool = _apply_routing_policy(_chat_candidates(session), policy, session)
    candidates = _matching_models(model_id, pool, session)
    candidates = _apply_routing_policy(candidates, policy, session)
    if not candidates:
        suggestions = _suggest_models(model_id, pool)
        hint = (
            f" Did you mean: {', '.join(suggestions)}?"
            if suggestions
            else " Call GET /v1/models to list verified available ids."
        )
        return [], f"Model '{model_id}' not found or not currently available.{hint}"
    return candidates, None


def _request_candidate_models(
    model_id: str,
    session: Session,
    policy: EffectiveRoutingPolicy | None = None,
) -> tuple[list[Model], str | None]:
    """Resolve the requested route plus its ordered, policy-safe fallbacks."""
    policy = policy or _effective_routing_policy(None)
    routes = [model_id, *policy.fallback_chain]
    candidates: list[Model] = []
    seen: set[str] = set()
    first_error: str | None = None
    for route in routes:
        route_candidates, error = _single_route_candidates(route, session, policy)
        if first_error is None and error:
            first_error = error
        for candidate in route_candidates:
            if candidate.id not in seen:
                seen.add(candidate.id)
                candidates.append(candidate)
    if candidates:
        return candidates, None
    if policy.provider_whitelist or policy.provider_blacklist or policy.min_context:
        return [], "No verified available models satisfy the effective routing policy"
    return [], first_error or f"No verified available models for {model_id}"


def _resolve_smart_model(session: Session):
    """Auto-select the largest (generally most capable) available model.

    ``auto:smart`` defaults to text chat models and sorts by health bucket
    first, then by descending ``param_size`` — so the biggest healthy text model
    wins. If no text model is available, it falls back to any chat-eligible
    category. Models with no known param_size sort last within their bucket.
    """
    candidates = _auto_candidate_models("smart", session)
    # health bucket ascending, then param_size descending (None last).
    candidates.sort(key=lambda m: (
        _HEALTH_ORDER.get(m.health_status, 3),
        -(m.param_size or 0),
    ))
    return _pick_first_bindable(candidates, session)


def _resolve_fast_model(session: Session):
    """Auto-select the fastest available text chat model.

    ``auto:fast`` is the latency-first counterpart to ``auto:smart``. Generic
    chat routes default to text models; callers can explicitly request
    ``auto:vision`` or ``auto:code`` when those categories are desired.
    """
    candidates = _auto_candidate_models("fast", session)
    return _pick_best(candidates, session)


def _pick_first_bindable(candidates: list[Model], session: Session):
    """Return the first candidate whose channel can be bound, or all-None."""
    for model in candidates:
        result = _try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]
    return None, None, None, None


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
    status = "healthy"
    error_code = None
    try:
        async for line in response.aiter_lines():
            yield line + "\n\n"
            if line.startswith("data: [DONE]"):
                break
    except Exception:
        status = "down"
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
) -> dict[str, str]:
    headers: dict[str, str] = {}
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
        headers["X-AC-Fallback-Count"] = str(max(0, len(attempted_models) - 1))
    if retry_after is not None:
        headers["X-AC-Retry-After"] = str(retry_after)
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
):
    error: dict = {"message": message, "type": error_type, "code": code}
    if param:
        error["param"] = param
    if retry_after is not None:
        error["retry_after"] = retry_after
    if attempted_models is not None:
        error["attempted_models"] = attempted_models
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=_diagnostic_headers(
            route=route,
            attempted_models=attempted_models,
            retry_after=retry_after,
        ),
    )


def _make_openai_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str = "invalid_request",
):
    return _make_ac_error(status_code, message, error_type, code, param=param)


def _model_route_eligible(model: Model, session: Session) -> bool:
    channel = session.get(Channel, model.channel_id)
    return (
        model.is_active is True
        and model.is_free is True
        # slow models are still callable (just >1s latency); the chat router
        # already treats them as candidates, so eligibility must match.
        and model.health_status in ("healthy", "slow")
        and not _is_cooling_down(model)
        and _is_pool_eligible(model, session)
        and _channel_route_eligible(channel)
    )


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
    body: SelfTestRequest | None = None,
    session: Session = Depends(get_session),
    auth=Depends(verify_token_or_apikey),
):
    """Non-consuming integration self-test for third-party clients."""
    route = (body.model if body else "auto:text")
    policy = _effective_routing_policy(auth, body.routing_policy if body else None)
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
        )

    policy = _effective_routing_policy(auth, body.routing_policy)
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
        )

    attempted: list[str] = []
    primary_candidates, _ = _single_route_candidates(body.model, session, policy)
    primary_candidate_ids = {candidate.id for candidate in primary_candidates}
    max_attempts = min(_MAX_UPSTREAM_ATTEMPTS, len(candidate_models))
    original_model = body.model
    last_rate_retry_after: int | None = None
    last_upstream_status: int | None = None
    busy_models: list[str] = []
    budget_limited: list[str] = []
    budget_retry_after: int | None = None
    failed_channels: set[str] = set()

    for candidate_index, model in enumerate(candidate_models[:max_attempts]):
        if model.channel_id in failed_channels:
            continue
        binding = _try_bind_model(model, session)
        if not binding:
            continue
        channel, adapter, key = binding
        try:
            _check_model_budget(model, session)
        except ModelBudgetExceeded as exc:
            budget_limited.append(model.model_id)
            budget_retry_after = max(budget_retry_after or 0, exc.retry_after)
            continue
        slot_key, acquired = await _try_acquire_model_slot(channel, model)
        if not acquired:
            busy_models.append(model.model_id)
            continue
        attempted.append(model.model_id)
        body.model = model.model_id
        payload = _build_openai_payload(body)

        base_url = channel.base_url or adapter.default_base_url
        url = f"{base_url}/chat/completions"
        headers = _upstream_headers(adapter, key)

        start = time.monotonic()
        if body.stream:
            client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
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
                        ),
                    },
                )

            error_body = await response.aread()
            await client.aclose()
            _release_model_slot(slot_key)
            ms = int((time.monotonic() - start) * 1000)
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
            async with httpx.AsyncClient(timeout=120.0) as client:
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
                ),
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
    if not attempted and busy_models:
        return _make_ac_error(
            503,
            "All candidate models are currently busy",
            "service_unavailable",
            "all_candidates_busy",
            attempted_models=busy_models,
            route=original_model,
        )
    if not attempted and budget_limited:
        return _make_ac_error(
            429,
            "All candidate models are locally rate limited before upstream call",
            "rate_limit_error",
            "local_model_budget_exceeded",
            retry_after=budget_retry_after,
            attempted_models=budget_limited,
            route=original_model,
        )
    if last_upstream_status == 429:
        return _make_ac_error(
            429,
            "All attempted candidate free models are currently rate limited",
            "rate_limit_error",
            "all_candidates_rate_limited",
            retry_after=last_rate_retry_after,
            attempted_models=attempted,
            route=original_model,
        )
    if last_upstream_status in (401, 403):
        error_code = "upstream_auth_failed"
    elif last_upstream_status and last_upstream_status >= 500:
        error_code = "upstream_server_error"
    else:
        error_code = "upstream_error"
    return _make_ac_error(
        last_upstream_status or 503,
        "No verified candidate model could complete the request",
        "upstream_error",
        error_code,
        attempted_models=attempted,
        route=original_model,
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

    policy = _effective_routing_policy(auth, body.routing_policy)
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
