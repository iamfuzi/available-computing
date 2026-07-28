"""Candidate pool generation, model matching, and route resolution.

Extracted from ``api/proxy.py`` verbatim. This module turns a requested
``model`` string (concrete id or ``auto:*`` prefix) plus an
:class:`EffectiveRoutingPolicy` into an ordered list of candidate ``Model``
rows, ready for the HTTP-layer fallback loop to iterate over.

The HTTP fallback loop itself (the per-candidate upstream call, streaming,
health feedback, semaphore acquisition) stays in ``api/proxy.py`` because it
is tightly coupled to httpx and process-local state. Only the pure
candidate-selection logic lives here.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from adapters import get_adapter
from api.channels import _decrypt_key
from models import Channel, Model

from . import scoring
from .policy import EffectiveRoutingPolicy, apply_routing_policy, effective_routing_policy

_VALID_CATEGORIES = {"text", "vision", "code", "embedding", "image", "video"}
AUTO_RE = re.compile(r"^auto:(" + "|".join(_VALID_CATEGORIES) + r"|smart|fast)$")

# Suffix tokens that mark a chat-tuning variant, not a different model family.
# Stripping these lets "qwen2.5-72b-instruct" normalize to the same key as a
# bare "qwen2.5-72b" without also collapsing genuinely different ids.
_VARIANT_SUFFIXES = {
    "instruct", "chat", "it", "base", "preview", "latest",
}

# Trailing context-window markers like "-128k" / "-32k" / "-8k" are dropped.
_CONTEXT_RE = re.compile(r"-\d+k$", re.IGNORECASE)


def channel_route_eligible(channel: Channel | None) -> bool:
    if not channel or not channel.enabled or channel.status != "active":
        return False
    if channel.key_expires_at:
        expires_at = channel.key_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return False
    return True


def try_bind_model(model: Model, session: Session):
    """Try to bind a model to its channel, adapter, and decrypted key."""
    channel = session.get(Channel, model.channel_id)
    if not channel_route_eligible(channel):
        return None
    adapter = get_adapter(channel.provider_type)
    key = _decrypt_key(channel.api_key_enc, session)
    return channel, adapter, key


def normalize_model_id(model_id: str) -> str:
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


def chat_candidates(session: Session, free_only: bool = True):
    """All active, routeable chat models not in rate-limit cooldown.

    ``free_only`` defaults True (the legacy free-pool behaviour). A profile
    with ``free_only: false`` passes False here to admit paid models as
    fallback — the only path that can widen the pool beyond free models.
    """
    stmt = (
        select(Model)
        .where(Model.is_active == True)
        .where(Model.health_status.in_(["healthy", "slow"]))
    )
    if free_only:
        stmt = stmt.where(Model.is_free == True)
    rows = session.exec(stmt).all()
    return [m for m in rows if scoring.is_pool_eligible(m, session) and not scoring.is_cooling_down(m)]


def category_candidates(
    session: Session,
    category: str,
    policy: EffectiveRoutingPolicy | None = None,
    free_only: bool = True,
):
    """All active, routable models of a category (e.g. image, embedding).

    Unlike ``chat_candidates`` this does NOT exclude the non-chat categories —
    it scopes to exactly one. Successful generation probes commonly exceed the
    fast-response threshold, so both healthy and slow models remain routable.
    """
    stmt = (
        select(Model)
        .where(Model.is_active == True)
        .where(Model.health_status.in_(["healthy", "slow"]))
        .where(Model.category == category)
    )
    if free_only:
        stmt = stmt.where(Model.is_free == True)
    rows = session.exec(stmt).all()
    candidates = [m for m in rows if not scoring.is_cooling_down(m)]
    if policy:
        candidates = apply_routing_policy(candidates, policy, session)
    return candidates


def matching_models(
    model_id: str,
    candidates: list[Model],
    session: Session,
    prefer_short_id: bool = False,
) -> list[Model]:
    """Return matching candidates sorted like pick_best, without binding."""
    tier1 = [m for m in candidates if m.model_id == model_id]
    if tier1:
        tier1.sort(key=lambda m: scoring.route_score_key(m, session))
        return tier1

    lowered = model_id.lower()
    tier2 = [m for m in candidates if m.model_id.lower() == lowered]
    if tier2:
        tier2.sort(
            key=lambda m: (
                scoring.HEALTH_ORDER.get(m.health_status, 3),
                len(m.model_id),
                scoring.route_score_key(m, session),
            )
        )
        return tier2

    norm = normalize_model_id(model_id)
    if norm:
        tier3 = [m for m in candidates if normalize_model_id(m.model_id) == norm]
        if tier3:
            tier3.sort(
                key=lambda m: (
                    scoring.HEALTH_ORDER.get(m.health_status, 3),
                    len(m.model_id),
                    scoring.route_score_key(m, session),
                )
            )
            return tier3

    return []


def suggest_models(model_id: str, all_models: list[Model], limit: int = 5) -> list[str]:
    """Return closest available model ids for a friendlier 404 message."""
    needle = normalize_model_id(model_id)
    scored: list[tuple[int, str]] = []
    for m in all_models:
        norm = normalize_model_id(m.model_id)
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


def auto_candidate_models(kind: str, session: Session, free_only: bool = True) -> list[Model]:
    chat = chat_candidates(session, free_only=free_only)
    text_candidates = [m for m in chat if scoring.is_generic_text_candidate(m)]
    generic_candidates = text_candidates or chat
    if kind == "smart":
        # auto:smart = "give me the most capable model". Keep deterministic
        # param_size ordering within each tier — the user explicitly asked for
        # the biggest, so randomizing would violate that intent.
        candidates = generic_candidates
        candidates.sort(key=lambda m: scoring.route_score_key(m, session, smart=True))
        return candidates
    if kind == "fast":
        candidates = generic_candidates
        candidates.sort(key=lambda m: scoring.route_score_key(m, session))
        return candidates
    candidates = [m for m in chat if (m.category or "text") == kind]
    candidates.sort(key=lambda m: scoring.route_score_key(m, session))
    return candidates


def single_route_candidates(
    model_id: str,
    session: Session,
    policy: EffectiveRoutingPolicy,
) -> tuple[list[Model], str | None]:
    free_only = policy.free_only
    auto_match = AUTO_RE.match(model_id)
    if auto_match:
        kind = auto_match.group(1)
        candidates = auto_candidate_models(kind, session, free_only=free_only)
        candidates = apply_routing_policy(
            candidates,
            policy,
            session,
            preserve_smart_order=kind == "smart",
        )
        if not candidates:
            return [], f"No verified available models for {model_id}"
        return candidates, None

    pool = apply_routing_policy(chat_candidates(session, free_only=free_only), policy, session)
    candidates = matching_models(model_id, pool, session)
    candidates = apply_routing_policy(candidates, policy, session)
    if not candidates:
        suggestions = suggest_models(model_id, pool)
        hint = (
            f" Did you mean: {', '.join(suggestions)}?"
            if suggestions
            else " Call GET /v1/models to list verified available ids."
        )
        return [], f"Model '{model_id}' not found or not currently available.{hint}"
    return candidates, None


def request_candidate_models(
    model_id: str,
    session: Session,
    policy: EffectiveRoutingPolicy | None = None,
) -> tuple[list[Model], str | None]:
    """Resolve the requested route plus its ordered, policy-safe fallbacks."""
    policy = policy or effective_routing_policy(None)
    routes = [model_id, *policy.fallback_chain]
    candidates: list[Model] = []
    seen: set[str] = set()
    first_error: str | None = None
    for route in routes:
        route_candidates, error = single_route_candidates(route, session, policy)
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


def classify_auto_route_unavailability(
    model_id: str,
    session: Session,
    policy: EffectiveRoutingPolicy,
) -> tuple[str, int | None]:
    """Explain why an ``auto:*`` route produced no callable candidates.

    Candidate selection intentionally removes unhealthy and cooling models.
    The HTTP layer still needs to distinguish three materially different
    outcomes for callers:

    - ``no_eligible_model``: no active model satisfies the hard policy;
    - ``rate_limited``: a matching model exists and has a live cooldown;
    - ``temporarily_unavailable``: matching models exist but are down,
      unverified, or attached to an unavailable channel.

    Returns ``(kind, retry_after_seconds)``. The retry value is only populated
    for ``rate_limited`` and points to the earliest model cooldown expiry.
    """
    auto_match = AUTO_RE.match(model_id)
    if not auto_match:
        return "no_eligible_model", None

    stmt = select(Model).where(Model.is_active == True)
    if policy.free_only:
        stmt = stmt.where(Model.is_free == True)
    rows = session.exec(stmt).all()
    chat = [model for model in rows if scoring.is_pool_eligible(model, session)]

    kind = auto_match.group(1)
    if kind in {"smart", "fast"}:
        text_candidates = [model for model in chat if scoring.is_generic_text_candidate(model)]
        candidates = text_candidates or chat
    else:
        candidates = [model for model in chat if (model.category or "text") == kind]

    candidates = apply_routing_policy(candidates, policy, session)
    if not candidates:
        return "no_eligible_model", None

    now = datetime.now(timezone.utc)
    retry_after_values: list[int] = []
    for model in candidates:
        if not scoring.is_cooling_down(model) or model.rate_limited_until is None:
            continue
        until = model.rate_limited_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        retry_after_values.append(max(1, math.ceil((until - now).total_seconds())))

    if retry_after_values:
        return "rate_limited", min(retry_after_values)
    return "temporarily_unavailable", None


# ---------------------------------------------------------------------------
# Binding helpers — combine candidate selection with channel/adapter/key binding.
# These return (model, channel, adapter, key) tuples or all-None.
# ---------------------------------------------------------------------------


def pick_best(candidates: list[Model], session: Session, prefer_short_id: bool = False):
    """Sort by health then latency, return first bindable model + (channel, adapter, key).

    When ``prefer_short_id`` is set (used for tolerant/fuzzy matching tiers), models
    with a shorter id (no org/variant prefix like ``LoRA/`` or ``Pro/``) are preferred
    over equally-healthy prefixed siblings, so a bare ``qwen2.5-7b`` resolves to
    ``Qwen/Qwen2.5-7B-Instruct`` rather than ``LoRA/Qwen/Qwen2.5-7B-Instruct``.
    """
    def sort_key(m: Model):
        priority = scoring.HEALTH_ORDER.get(m.health_status, 3)
        ms = m.last_response_ms if m.last_response_ms is not None else 999999
        if prefer_short_id:
            # health bucket first, then prefer shorter id (no org/variant prefix),
            # then latency. This avoids a bare "qwen2.5-7b" resolving to a
            # slightly-faster "LoRA/..." variant instead of the base model.
            return (priority, len(m.model_id), ms, m.model_id)
        return scoring.route_score_key(m, session)

    candidates.sort(key=sort_key)
    for model in candidates:
        result = try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]
    return None, None, None, None


def pick_first_bindable(candidates: list[Model], session: Session):
    """Return the first candidate whose channel can be bound, or all-None."""
    for model in candidates:
        result = try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]
    return None, None, None, None


def resolve_from_candidates(model_id: str, candidates: list[Model], session: Session):
    """Find a model within ``candidates`` via tolerant three-tier matching.

    Shared by the chat router and the embedding/rerank routers so they all get
    the same fuzzy-matching behaviour (exact → case-insensitive → normalized).

    Returns (model, channel, adapter, key) or (None, None, None, None).
    """
    # Tier 1: exact
    tier1 = [m for m in candidates if m.model_id == model_id]
    found = pick_best(tier1, session)
    if found[0]:
        return found

    # Tier 2: case-insensitive (also tolerates org-prefix difference)
    lowered = model_id.lower()
    tier2 = [m for m in candidates if m.model_id.lower() == lowered]
    found = pick_best(tier2, session, prefer_short_id=True)
    if found[0]:
        return found

    # Tier 3: normalized core (drops org prefix + variant suffix).
    # Always run when normalized yields a real core; it may match even when
    # the input is already minimal (e.g. "qwen2.5-72b" -> "qwen2.5-72b").
    norm = normalize_model_id(model_id)
    if norm:
        tier3 = [m for m in candidates if normalize_model_id(m.model_id) == norm]
        found = pick_best(tier3, session, prefer_short_id=True)
        if found[0]:
            return found

    return None, None, None, None


def resolve_model(model_id: str, session: Session):
    """Find an active free healthy chat model and its channel.

    Matching is tolerant, in three tiers:
      1. exact ``model_id`` match
      2. case-insensitive match
      3. normalized match (lowercased, org-prefix dropped, variant suffix stripped)

    Returns (model, channel, adapter, key) or (None, None, None, None).
    """
    return resolve_from_candidates(model_id, chat_candidates(session), session)


def resolve_category_model(
    model_id: str,
    category: str,
    session: Session,
    policy: EffectiveRoutingPolicy | None = None,
):
    """Resolve a model within a single category (embedding/rerank) using the
    same tolerant matching as the chat router."""
    return resolve_from_candidates(
        model_id,
        category_candidates(session, category, policy),
        session,
    )


def resolve_auto_category_model(
    category: str,
    session: Session,
    policy: EffectiveRoutingPolicy | None = None,
):
    """Select and bind the best routable model in a non-chat category."""
    candidates = category_candidates(session, category, policy)
    candidates.sort(key=lambda model: scoring.route_score_key(model, session))
    for model in candidates:
        bound = try_bind_model(model, session)
        if bound:
            return model, bound[0], bound[1], bound[2]
    return None, None, None, None


def resolve_auto_model(category: str, session: Session):
    """Auto-select the best available model for a given category."""
    candidates = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status == "healthy")
        .where(Model.category == category)
    ).all()
    candidates = [m for m in candidates if not scoring.is_cooling_down(m)]

    candidates.sort(key=scoring.health_sort_key)

    for model in candidates:
        result = try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]

    return None, None, None, None


def resolve_smart_model(session: Session):
    """Auto-select the largest (generally most capable) available model.

    ``auto:smart`` defaults to text chat models and sorts by health bucket
    first, then by descending ``param_size`` — so the biggest healthy text model
    wins. If no text model is available, it falls back to any chat-eligible
    category. Models with no known param_size sort last within their bucket.
    """
    candidates = auto_candidate_models("smart", session)
    # health bucket ascending, then param_size descending (None last).
    candidates.sort(
        key=lambda m: (
            scoring.HEALTH_ORDER.get(m.health_status, 3),
            -(m.param_size or 0),
        )
    )
    return pick_first_bindable(candidates, session)


def resolve_fast_model(session: Session):
    """Auto-select the fastest available text chat model.

    ``auto:fast`` is the latency-first counterpart to ``auto:smart``. Generic
    chat routes default to text models; callers can explicitly request
    ``auto:vision`` or ``auto:code`` when those categories are desired.
    """
    candidates = auto_candidate_models("fast", session)
    return pick_best(candidates, session)


def model_route_eligible(model: Model, session: Session) -> bool:
    channel = session.get(Channel, model.channel_id)
    return (
        model.is_active is True
        and model.is_free is True
        # slow models are still callable (just >1s latency); the chat router
        # already treats them as candidates, so eligibility must match.
        and model.health_status in ("healthy", "slow")
        and not scoring.is_cooling_down(model)
        and scoring.is_pool_eligible(model, session)
        and channel_route_eligible(channel)
    )
