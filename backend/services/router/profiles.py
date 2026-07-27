"""Routing profiles — named, reusable routing policies loaded from YAML.

A profile captures the routing constraints a caller project wants applied to
every request it makes, so the project does not have to repeat the full
constraint set in each request body. Example (``profiles/hotspot-classifier.yaml``):

.. code-block:: yaml

    task: classification
    objective: latency
    free_only: true
    provider_denylist: [google, gemini]
    model_deny_patterns: [gemini, glm-z1, reasoning]
    max_attempts: 3
    max_attempts_per_provider: 1
    deadline_ms: 45000

At request time the caller sends ``routing_policy.profile: "hotspot-classifier"``.
The profile's constraints become the **baseline**; the request body and the
caller's ApiKey may only narrow them further (same monotone-narrowing rule as
the rest of the routing policy), never widen them. A request that names a
profile the ApiKey is not authorized for is rejected with ``policy_rejected``.

Profiles are optional: if ``PROFILES_PATH`` is empty or missing, every request
falls back to the existing per-key/per-request policy and behaviour is
unchanged. This keeps the feature strictly additive.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from config import PROFILES_PATH


@dataclass(frozen=True)
class RoutingProfile:
    """A reusable, named set of routing constraints.

    Only fields that are actually enforced by the router live here. Fields
    that were considered but not implemented (objective, task,
    max_observed_latency_ms) were removed rather than left as silent no-ops —
    an unenforced config field is worse than a missing one because it misleads
    the caller into thinking their constraint took effect.
    """

    name: str
    free_only: bool = True
    provider_denylist: tuple[str, ...] = ()
    provider_whitelist: tuple[str, ...] = ()
    model_deny_patterns: tuple[str, ...] = ()
    max_attempts: Optional[int] = None
    max_attempts_per_provider: Optional[int] = None
    deadline_ms: Optional[int] = None

    def provider_denied(self, provider_type: str) -> bool:
        """True if this provider is on the profile's denylist."""
        return provider_type in self.provider_denylist

    def model_denied(self, model_id: str) -> bool:
        """True if the model id matches any deny pattern (case-insensitive substring)."""
        lower = model_id.lower()
        return any(pat.lower() in lower for pat in self.model_deny_patterns)


# Fields that used to exist but were removed because they were not enforced.
# Listing them lets us reject them explicitly at load time with a clear message
# instead of silently ignoring a constraint the caller believes is active.
_REMOVED_FIELDS = {"objective", "task", "max_observed_latency_ms"}

# Module-level registry, populated at import time. Mirrors the
# adapters/registry.py pattern: load once, fail fast on bad config.
_profiles: dict[str, RoutingProfile] = {}


def _parse_profile(name: str, raw: dict) -> RoutingProfile:
    """Validate a single profile dict and build a RoutingProfile.

    Raises ValueError on any schema violation so a misconfigured profile
    aborts startup rather than silently degrading routing.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"profile '{name}' must be a mapping, got {type(raw).__name__}")

    # Reject removed fields loudly. A caller who sets `objective: quality`
    # expects it to influence scoring; silently ignoring it would be a bug.
    removed_present = _REMOVED_FIELDS & set(raw.keys())
    if removed_present:
        raise ValueError(
            f"profile '{name}' uses removed field(s) {sorted(removed_present)}; "
            f"these were never enforced — remove them (planned for a future version)"
        )

    def _as_str_tuple(key: str) -> tuple[str, ...]:
        value = raw.get(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"profile '{name}'.{key} must be a list of strings")
        return tuple(value)

    free_only = raw.get("free_only", True)
    if not isinstance(free_only, bool):
        raise ValueError(f"profile '{name}'.free_only must be a boolean")

    for int_key in ("max_attempts", "max_attempts_per_provider", "deadline_ms"):
        val = raw.get(int_key)
        if val is not None and (not isinstance(val, int) or val <= 0):
            raise ValueError(f"profile '{name}'.{int_key} must be a positive integer or null")

    return RoutingProfile(
        name=name,
        free_only=free_only,
        provider_denylist=_as_str_tuple("provider_denylist"),
        provider_whitelist=_as_str_tuple("provider_whitelist"),
        model_deny_patterns=_as_str_tuple("model_deny_patterns"),
        max_attempts=raw.get("max_attempts"),
        max_attempts_per_provider=raw.get("max_attempts_per_provider"),
        deadline_ms=raw.get("deadline_ms"),
    )


def _load_profiles(path: Path) -> dict[str, RoutingProfile]:
    """Load every ``*.yaml`` profile under ``path``. Returns {} if absent/empty.

    Duplicate names (across files) and schema errors raise at load time.
    """
    if not path.exists() or not path.is_dir():
        return {}
    registry: dict[str, RoutingProfile] = {}
    for yaml_file in sorted(path.glob("*.yaml")):
        name = yaml_file.stem
        try:
            with open(yaml_file) as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"profile '{name}' ({yaml_file}) is not valid YAML: {exc}") from exc
        if raw is None:
            raw = {}
        if name in registry:
            raise ValueError(f"duplicate routing profile name: '{name}'")
        registry[name] = _parse_profile(name, raw)
    return registry


# Load eagerly at import — a broken profile should crash startup, not the
# first request that references it. (Mirrors adapters/registry.py.)
_profiles = _load_profiles(PROFILES_PATH)


def load_profile(name: str) -> RoutingProfile | None:
    """Return the profile named ``name``, or None if it does not exist."""
    return _profiles.get(name)


def list_profiles() -> list[str]:
    """Return the names of all loaded profiles (for diagnostics/admin)."""
    return sorted(_profiles.keys())


def profile_exists(name: str) -> bool:
    return name in _profiles


def is_profile_authorized(api_key, profile_name: str) -> bool:
    """Whether ``api_key`` may use the named profile.

    Authorization is stored on the ApiKey row as ``allowed_profiles`` (a JSON
    array of profile names). An empty/None list means "all profiles allowed"
    (open by default, for personal single-user deployments). A non-empty list
    is an explicit allowlist.
    """
    import json

    profile = load_profile(profile_name)
    if profile is None:
        return False
    if api_key is None:
        # Unauthenticated/admin paths (e.g. JWT) bypass profile authorization.
        return True
    raw = getattr(api_key, "allowed_profiles", None)
    if not raw:
        # No explicit allowlist = all profiles permitted (personal deployment default).
        return True
    try:
        allowed = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(allowed, list):
        return False
    return profile_name in allowed
