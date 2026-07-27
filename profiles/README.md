# Routing Profiles

A routing profile is a named, reusable set of routing constraints that a
caller project applies to every request it makes. Profiles let multiple
projects share one AC instance while each gets a tailored candidate pool,
denylist, and fallback budget — without repeating the full constraint set in
each request body.

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
| `task` | string \| null | null | Semantic task type (`classification`, `summary`, `code`). Reserved for future quality scoring; recorded in traces. |
| `objective` | enum | `latency` | One of `latency`, `quality`, `cost`, `balanced`. |
| `free_only` | bool | `true` | Only route to free channels. |
| `provider_denylist` | list[string] | `[]` | Whole-provider exclusions (hard filter). Values match `channel.provider_type`. |
| `provider_whitelist` | list[string] | `[]` | Restrict to these providers (intersects the key's whitelist). |
| `model_deny_patterns` | list[string] | `[]` | Model-id substring exclusions, case-insensitive (hard filter). |
| `max_observed_latency_ms` | int \| null | null | Soft latency target; penalizes slow candidates in scoring but does not exclude them. |
| `max_attempts` | int \| null | null | Max upstream tries within one request. |
| `max_attempts_per_provider` | int \| null | null | Max tries per provider in one request. Set to 1 to force cross-provider fan-out. |
| `deadline_ms` | int \| null | null | Hard wall-clock deadline for the whole request including fallback. |

## Hard vs soft constraints

`provider_denylist`, `model_deny_patterns`, `free_only`, and `min_context`
are **hard filters**: a candidate that violates them is dropped outright, and
the fallback chain cannot bring it back (every route re-applies the filter).

`max_observed_latency_ms` is a **soft** constraint: it penalizes a candidate's
score but does not exclude it.

## Example

See [`hotspot-classifier.yaml`](./hotspot-classifier.yaml) for a complete,
commented profile used by the hotspot-pipeline classification service.
