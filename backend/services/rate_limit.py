"""
Parse rate limit headers from provider API responses.
Updates are stored as observed rate limits, which take priority over manual whitelist entries.
"""
from typing import Optional
from httpx import Response


def parse_rate_limit_headers(response: Response) -> Optional[dict]:
    """
    Extract rate limit info from response headers.
    Returns a dict like {"rpm": 30, "rpd": 1500} or None.
    """
    headers = {k.lower(): v for k, v in response.headers.items()}
    limits: dict = {}

    # Groq style: x-ratelimit-limit-requests, x-ratelimit-limit-tokens
    if "x-ratelimit-limit-requests" in headers:
        try:
            limits["rpm"] = int(headers["x-ratelimit-limit-requests"])
        except ValueError:
            pass
    if "x-ratelimit-limit-tokens" in headers:
        try:
            limits["tpm"] = int(headers["x-ratelimit-limit-tokens"])
        except ValueError:
            pass

    # SiliconFlow / OpenAI compatible style
    if "x-ratelimit-limit-rpm" in headers:
        try:
            limits["rpm"] = int(headers["x-ratelimit-limit-rpm"])
        except ValueError:
            pass
    if "x-ratelimit-limit-tpm" in headers:
        try:
            limits["tpm"] = int(headers["x-ratelimit-limit-tpm"])
        except ValueError:
            pass
    if "x-ratelimit-limit-rpd" in headers:
        try:
            limits["rpd"] = int(headers["x-ratelimit-limit-rpd"])
        except ValueError:
            pass

    # Generic rate-limit headers (RFC 6585 style)
    if not limits and "ratelimit-limit" in headers:
        try:
            # Format: "30, 60;window=60" or just "30"
            val = headers["ratelimit-limit"].split(";")[0].split(",")[0].strip()
            limits["rpm"] = int(val)
        except (ValueError, IndexError):
            pass

    return limits if limits else None


def parse_remaining_headers(response: Response) -> Optional[dict]:
    """Extract remaining quota from response headers."""
    headers = {k.lower(): v for k, v in response.headers.items()}
    remaining: dict = {}

    for key, field in [
        ("x-ratelimit-remaining-requests", "rpm_remaining"),
        ("x-ratelimit-remaining-tokens", "tpm_remaining"),
        ("x-ratelimit-remaining-rpd", "rpd_remaining"),
        ("x-ratelimit-remaining", "rpm_remaining"),
    ]:
        if key in headers:
            try:
                remaining[field] = int(headers[key])
            except ValueError:
                pass

    return remaining if remaining else None
