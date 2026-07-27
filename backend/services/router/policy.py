"""Routing policy model and enforcement.

Extracted from ``api/proxy.py`` verbatim. A routing policy expresses the
hard/soft constraints that shrink and reorder the candidate pool:

- provider whitelist/blacklist (hard)
- min context window (hard)
- prefer latency vs capability (soft sort)
- an explicit fallback chain of concrete model ids

Policies are merged from two sources: the caller's ``ApiKey`` row (the
baseline the key is trusted with) and the per-request ``RoutingPolicy`` body.
The merge is monotone — a request may only *narrow* its key's permissions,
never widen them (e.g. request can add to the blacklist but not remove from
it; request can raise min_context but not lower it). This invariant is what
makes the routing policy safe to expose to third-party callers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from models import ApiKey, Channel, Model

from . import scoring
from .profiles import RoutingProfile


class RoutingPolicy(BaseModel):
    """Per-request routing policy body.

    Authored by the caller; always merged with the caller's ApiKey baseline
    via :func:`effective_routing_policy`. Fields here can only narrow the
    key's effective permissions, never widen them.

    ``profile`` names a server-side routing profile (see profiles.py) whose
    constraints become the merge baseline. The profile is loaded and
    authorized by the HTTP entrypoint before this merge runs.
    """

    model_config = ConfigDict(extra="forbid")
    profile: Optional[str] = Field(default=None, max_length=64)
    exclude: list[str] = Field(default_factory=list, max_length=50)
    min_context: Optional[int] = Field(default=None, ge=1)
    prefer: Optional[Literal["latency", "capability"]] = None
    fallback_chain: list[str] = Field(default_factory=list, max_length=10)


@dataclass(frozen=True)
class EffectiveRoutingPolicy:
    """Fully-merged, immutable routing policy ready for candidate filtering.

    The ``model_deny_patterns`` / ``max_attempts`` / ``max_attempts_per_provider``
    fields are populated only when a routing profile is in effect; they default
    to empty/None so callers that never use profiles see unchanged behaviour.
    """

    provider_whitelist: frozenset[str]
    provider_blacklist: frozenset[str]
    min_context: int | None
    prefer: str
    fallback_chain: tuple[str, ...]
    # Profile-sourced extensions (stage 3/4). Empty/None when no profile.
    model_deny_patterns: frozenset[str] = frozenset()
    max_attempts: int | None = None
    max_attempts_per_provider: int | None = None
    profile_name: str | None = None


def parse_provider_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    return {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()


def effective_routing_policy(
    api_key: ApiKey | None,
    request_policy: RoutingPolicy | None = None,
    profile: RoutingProfile | None = None,
) -> EffectiveRoutingPolicy:
    """Merge the ApiKey baseline, optional profile, and per-request policy.

    Merge order (each layer may only narrow the previous):

        profile (server baseline)  ≤  ApiKey row  ≤  request body

    Concretely, blacklists and model-deny-patterns are unioned across all
    three layers; whitelists intersect; min_context takes the max; prefer
    resolves to the most specific non-None value. The profile's
    ``max_attempts`` / ``max_attempts_per_provider`` are inherited directly
    (the request body cannot widen the fallback budget a profile grants).
    """
    whitelist = parse_provider_ids(api_key.provider_whitelist) if api_key else set()
    blacklist = parse_provider_ids(api_key.provider_blacklist) if api_key else set()
    deny_patterns: set[str] = set()
    max_attempts: int | None = None
    max_attempts_per_provider: int | None = None
    profile_name: str | None = None

    # Layer 1: profile baseline.
    if profile:
        profile_name = profile.name
        blacklist.update(profile.provider_denylist)
        deny_patterns.update(profile.model_deny_patterns)
        if profile.provider_whitelist:
            # Intersect: a profile whitelist narrows the key's whitelist.
            if whitelist:
                whitelist &= set(profile.provider_whitelist)
            else:
                whitelist = set(profile.provider_whitelist)
        max_attempts = profile.max_attempts
        max_attempts_per_provider = profile.max_attempts_per_provider

    # Layer 2: ApiKey baseline.
    key_min_context = api_key.default_min_context if api_key else None
    request_min_context = request_policy.min_context if request_policy else None
    minimums = [value for value in (key_min_context, request_min_context) if value is not None]

    # Layer 3: request body (may only narrow).
    if request_policy:
        blacklist.update(request_policy.exclude)

    return EffectiveRoutingPolicy(
        provider_whitelist=frozenset(whitelist),
        provider_blacklist=frozenset(blacklist),
        min_context=max(minimums) if minimums else None,
        prefer=(request_policy.prefer if request_policy and request_policy.prefer else None)
        or (api_key.default_prefer if api_key else "latency"),
        fallback_chain=tuple(request_policy.fallback_chain if request_policy else ()),
        model_deny_patterns=frozenset(deny_patterns),
        max_attempts=max_attempts,
        max_attempts_per_provider=max_attempts_per_provider,
        profile_name=profile_name,
    )


def apply_routing_policy(
    candidates: list[Model],
    policy: EffectiveRoutingPolicy,
    session: Session,
    *,
    preserve_smart_order: bool = False,
) -> list[Model]:
    """Filter ``candidates`` by the hard constraints, then optionally re-sort.

    Hard filters (provider whitelist/blacklist, model deny patterns, min
    context) drop candidates outright — the fallback chain cannot bypass
    them because every route re-runs this filter. The soft
    ``prefer=capability`` re-sort promotes larger models unless
    ``preserve_smart_order`` is set (used by ``auto:smart`` which has its own
    deterministic ordering).
    """
    deny_patterns = policy.model_deny_patterns
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
        if deny_patterns:
            lower_id = model.model_id.lower()
            if any(pat.lower() in lower_id for pat in deny_patterns):
                continue
        if policy.min_context is not None and (
            model.context_length is None or model.context_length < policy.min_context
        ):
            continue
        allowed.append(model)
    if policy.prefer == "capability" and not preserve_smart_order:
        allowed.sort(key=lambda model: scoring.route_score_key(model, session, smart=True))
    return allowed
