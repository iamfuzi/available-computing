# Routing Profiles

A routing profile is a named, reusable set of routing constraints that a
caller project applies to every request it makes. Profiles let multiple
projects share one AC instance while each gets a tailored candidate pool,
denylist, and fallback budget — without repeating the full constraint set in
each request body.

For the caller-facing setup, SDK examples, errors, retries, and diagnostics,
see the [Application Integration Guide](../docs/06-integration.md).

## How it works

1. Put one YAML file per profile in this directory. The filename (without
   `.yaml`) is the profile name.
2. In a request, set `routing_policy.profile: "<name>"`.
3. AC loads the profile, checks the caller's ApiKey is authorized for it, and
   merges its constraints into the effective routing policy.

Profiles are **optional**. If this directory is empty or a request does not
name a profile, AC uses the legacy per-key / per-request policy and behaviour
is unchanged.

## Authorization

An ApiKey's `allowed_profiles` field (a JSON array) controls which profiles it
may use:

- `null` / empty → all profiles allowed (the personal-deployment default).
- `["hotspot-classifier"]` → only that profile; others return 403
  `policy_rejected`.

JWT/admin requests bypass profile authorization.

For an application-specific key, prefer a non-empty `allowed_profiles`
allowlist. The empty default is convenient for a personal instance, but it
also lets that key select any profile added later.

## Merge rule (monotone narrowing)

The profile is the **baseline**. The ApiKey row and the per-request body may
only *narrow* it, never widen it:

- `provider_denylist` and `model_deny_patterns` are **unioned** across all
  three layers.
- `provider_whitelist` is **intersected**.
- `min_context` takes the **max**.
- `max_attempts` / `max_attempts_per_provider` are inherited from the profile
  directly (the request cannot widen the fallback budget).

This is the same monotone-narrowing invariant the routing policy already uses
for the ApiKey-vs-request merge, extended with a profile layer underneath.

## Field reference

| Field | Type | Default | Description |
|---|---|---|---|
| `free_only` | bool | `true` | Only route to free channels. Set `false` to admit paid models as a last-resort fallback (a profile-granted escape hatch; the key/request cannot relax it). |
| `provider_denylist` | list[string] | `[]` | Whole-provider exclusions (hard filter). Values match `channel.provider_type`. |
| `provider_whitelist` | list[string] | `[]` | Restrict to these providers (intersects the key's whitelist). |
| `model_deny_patterns` | list[string] | `[]` | Model-id substring exclusions, case-insensitive (hard filter). |
| `max_attempts` | int \| null | null | Max upstream tries within one request. |
| `max_attempts_per_provider` | int \| null | null | Max tries per provider in one request. Set to 1 to force cross-provider fan-out. |
| `deadline_ms` | int \| null | null | Hard wall-clock budget for the whole request. Each upstream try gets at most `deadline_ms / max_attempts` seconds before timing out, so fallback cannot accumulate into a multi-minute hang. |

## Hard vs soft constraints

`provider_denylist`, `model_deny_patterns`, `free_only`, and `min_context`
are **hard filters**: a candidate that violates them is dropped outright, and
the fallback chain cannot bring it back (every route re-applies the filter).

`deadline_ms` bounds wall-clock time: it sets the per-try upstream timeout so
the total fallback sequence stays within budget.

## Caller contract

The AC administrator gives the caller a base URL, an `ac_` API key, a route,
and the profile name. The caller first validates the exact combination:

```bash
export AC_BASE_URL="https://ai.example.com/v1"
export AC_API_KEY="ac_your_key"
export AC_ROUTING_PROFILE="your-profile"

curl "$AC_BASE_URL/ac/self-test" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"auto:text\",
    \"routing_policy\": {\"profile\": \"$AC_ROUTING_PROFILE\"}
  }"
```

The business request must carry the same profile:

```bash
curl "$AC_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $AC_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"auto:text\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
    \"routing_policy\": {\"profile\": \"$AC_ROUTING_PROFILE\"}
  }"
```

An unknown profile returns `404 profile_not_found`; a key outside the
allowlist returns `403 profile_unauthorized`. Profile files are loaded by the
AC process, so restart AC after adding or changing one. Callers should not
duplicate the profile's provider/model fallback logic locally.

## Fields that look useful but are not (yet) supported

`objective`, `task`, and `max_observed_latency_ms` were considered but removed
because they were never enforced. If a profile sets them, AC rejects it at
load time with a clear error rather than silently ignoring the constraint —
an unenforced config field is worse than a missing one. They may return in a
future version once the scoring work to honour them is built.

## Example

See [`hotspot-classifier.yaml`](./hotspot-classifier.yaml) for a complete,
commented profile used by the hotspot-pipeline classification service.
