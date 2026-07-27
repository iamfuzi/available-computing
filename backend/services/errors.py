"""Standardized AC error responses.

Every proxy error returns a uniform JSON body so callers can parse the
failure programmatically instead of scraping response headers:

.. code-block:: json

    {
      "error": {
        "type": "routing_exhausted",
        "code": "all_candidates_unavailable",
        "message": "No candidate satisfied the routing policy",
        "retryable": true,
        "retry_after": 60,
        "scope": "routing_profile",
        "request_id": "ac_req_xxx",
        "attempted_models": ["provider-a/model-x", "provider-b/model-y"]
      }
    }

When ``retry_after`` is set, the standard ``Retry-After`` header is also
written (in addition to the legacy ``X-AC-Retry-After``) so generic HTTP
clients honor it without AC-specific knowledge.

The :func:`make_ac_error` builder is a drop-in superset of the old
``_make_ac_error``: it keeps the original ``type``/``code``/``param`` fields
callers already send, and layers on ``scope``/``request_id`` plus the
machine-friendly ``retryable`` flag derived from the error type.
"""
from __future__ import annotations

from typing import Optional

from fastapi.responses import JSONResponse

# Canonical error types. The set is closed: proxy code maps each failure to
# one of these so callers can switch on ``error.type`` deterministically.
ERROR_TYPES = {
    "policy_rejected",
    "no_eligible_model",
    "rate_limited",
    "routing_exhausted",
    "deadline_exceeded",
    "upstream_invalid_response",
    # Legacy OpenAI-style types still emitted for backward compatibility:
    "invalid_request_error",
    "authentication_error",
    "service_unavailable",
    "rate_limit_error",
}

# Error types that are always retryable. Everything else defaults to
# non-retryable unless the caller overrides via ``retryable=``.
_RETRYABLE_TYPES = {
    "rate_limited",
    "rate_limit_error",
    "routing_exhausted",
    "deadline_exceeded",
    "service_unavailable",
}

# Map an error type to the ``scope`` written into the body, when the caller
# does not supply one. Scope tells the caller *where* the failure happened so
# it knows whether to back off a single model, a provider, or its own policy.
_DEFAULT_SCOPE = {
    "rate_limited": "model",
    "rate_limit_error": "model",
    "routing_exhausted": "routing_profile",
    "no_eligible_model": "routing_profile",
    "policy_rejected": "routing_profile",
    "deadline_exceeded": "request",
    "service_unavailable": "upstream",
    "upstream_invalid_response": "upstream",
}


def _is_retryable(error_type: str, retryable: Optional[bool]) -> bool:
    if retryable is not None:
        return retryable
    return error_type in _RETRYABLE_TYPES


def make_ac_error(
    status_code: int,
    message: str,
    error_type: str,
    code: str,
    *,
    param: Optional[str] = None,
    retry_after: Optional[int] = None,
    retryable: Optional[bool] = None,
    scope: Optional[str] = None,
    request_id: Optional[str] = None,
    attempted_models: Optional[list[str]] = None,
    route: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """Build a standardized AC error JSONResponse.

    The body is a superset of the legacy shape (``message``/``type``/``code``/
    ``param``/``retry_after``/``attempted_models``) plus ``retryable``,
    ``scope`` and ``request_id``. Diagnostic ``X-AC-*`` headers mirror the body
    so header-only clients get the same information.
    """
    resolved_retryable = _is_retryable(error_type, retryable)
    resolved_scope = scope or _DEFAULT_SCOPE.get(error_type)

    error: dict = {
        "message": message,
        "type": error_type,
        "code": code,
        "retryable": resolved_retryable,
    }
    if param:
        error["param"] = param
    if retry_after is not None:
        error["retry_after"] = retry_after
    if resolved_scope:
        error["scope"] = resolved_scope
    if request_id:
        error["request_id"] = request_id
    if attempted_models is not None:
        error["attempted_models"] = attempted_models

    headers: dict[str, str] = {}
    if route:
        headers["X-AC-Route"] = route
    if request_id:
        headers["X-AC-Request-ID"] = request_id
    if attempted_models is not None:
        headers["X-AC-Attempted-Models"] = ",".join(attempted_models)
        headers["X-AC-Attempt-Count"] = str(len(attempted_models))
    if retry_after is not None:
        # X-AC-Retry-After is the legacy header; Retry-After is the RFC 7231
        # standard that generic HTTP clients honor automatically.
        headers["X-AC-Retry-After"] = str(retry_after)
        headers["Retry-After"] = str(retry_after)
    if extra_headers:
        headers.update(extra_headers)

    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=headers,
    )
