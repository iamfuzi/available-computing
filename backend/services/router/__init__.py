"""Routing layer for the OpenAI-compatible proxy.

Pure-Python candidate selection extracted out of ``api/proxy.py`` so that
policy, profile, and fallback-strategy changes have a clean home instead of
expanding the ~1770-line proxy module. The HTTP fallback loop (httpx calls,
streaming, health feedback, semaphores) remains in ``api/proxy.py``.

Modules:
- :mod:`scoring` — health ordering, cooling-down, success rate, route score.
- :mod:`policy`  — ``RoutingPolicy`` body, ``EffectiveRoutingPolicy`` merge.
- :mod:`candidates` — candidate pool generation, tolerant matching, route resolution.
"""
from __future__ import annotations

from .candidates import (
    AUTO_RE,
    auto_candidate_models,
    category_candidates,
    channel_route_eligible,
    chat_candidates,
    matching_models,
    model_route_eligible,
    normalize_model_id,
    pick_best,
    pick_first_bindable,
    request_candidate_models,
    resolve_auto_category_model,
    resolve_auto_model,
    resolve_category_model,
    resolve_fast_model,
    resolve_from_candidates,
    resolve_model,
    resolve_smart_model,
    single_route_candidates,
    suggest_models,
    try_bind_model,
)
from .policy import (
    EffectiveRoutingPolicy,
    RoutingPolicy,
    apply_routing_policy,
    effective_routing_policy,
    parse_provider_ids,
)
from .profiles import (
    RoutingProfile,
    is_profile_authorized,
    list_profiles,
    load_profile,
    profile_exists,
)
from . import scoring

__all__ = [
    # policy
    "RoutingPolicy",
    "EffectiveRoutingPolicy",
    "effective_routing_policy",
    "apply_routing_policy",
    "parse_provider_ids",
    # profiles
    "RoutingProfile",
    "load_profile",
    "list_profiles",
    "profile_exists",
    "is_profile_authorized",
    # candidates
    "AUTO_RE",
    "auto_candidate_models",
    "category_candidates",
    "channel_route_eligible",
    "chat_candidates",
    "matching_models",
    "model_route_eligible",
    "normalize_model_id",
    "pick_best",
    "pick_first_bindable",
    "request_candidate_models",
    "resolve_auto_category_model",
    "resolve_auto_model",
    "resolve_category_model",
    "resolve_fast_model",
    "resolve_from_candidates",
    "resolve_model",
    "resolve_smart_model",
    "single_route_candidates",
    "suggest_models",
    "try_bind_model",
    # scoring submodule
    "scoring",
]
