"""Routing scoring and candidate-shaping utilities.

Extracted from ``api/proxy.py`` verbatim — pure functions over ``Model`` /
``Channel`` rows plus a ``Session``. No HTTP, no asyncio, no process state.
These are the lowest-level building blocks of the routing layer: health
ordering, cooling-down checks, recent success rate, the route score key,
and the heuristics that decide whether a model is a generic text candidate.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from models import HealthRecord, Model

# Routing priority: healthy models are preferred, with slow models as fallback.
# Unknown/down and cooled-down rate-limited models stay out of automatic routing.
HEALTH_ORDER: dict[str, int] = {"healthy": 0, "slow": 1}

# Number of recent HealthRecords consulted when estimating a model's success
# rate for scoring. Kept small so the score reacts to the latest few calls.
RECENT_SCORE_LIMIT = 20

# Categories that are not chat-completion targets (excluded from model resolution)
NON_CHAT_CATEGORIES = {"audio", "image", "video", "embedding", "rerank", "guard"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_cooling_down(model: Model) -> bool:
    if not model.rate_limited_until:
        return False
    until = model.rate_limited_until
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now_utc()


def health_sort_key(model: Model) -> tuple:
    """Sort key: (health_priority, response_ms). Lower is better."""
    return (
        HEALTH_ORDER.get(model.health_status, 3),
        model.last_response_ms if model.last_response_ms is not None else 999999,
    )


def recent_success_rate(model: Model, session: Session) -> float:
    records = session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id == model.id)
        .order_by(HealthRecord.checked_at.desc())
        .limit(RECENT_SCORE_LIMIT)
    ).all()
    if not records:
        return 1.0 if model.health_status == "healthy" else 0.0
    good = sum(1 for r in records if r.status == "healthy")
    return good / len(records)


def recent_traffic_evidence(model: Model, session: Session) -> tuple[float, int | None]:
    """(success rate, real-traffic latency median) from the most recent records.

    One query serves both scoring signals. Success counts passive and active
    records alike — a probe pass is aliveness evidence. Latency, however, only
    trusts passive (real-traffic) records: probes send tiny payloads, so their
    response time says nothing about how the model serves a real request.
    Using probe latency for ranking made every freshly probed model jump to
    the front of its health bucket (a 550B model answered a 5-character
    question in 12s because a 200-token probe ran sub-second, 2026-10-02).
    Returns latency=None when the model has no real-traffic history yet.
    """
    records = session.exec(
        select(HealthRecord)
        .where(HealthRecord.model_id == model.id)
        .order_by(HealthRecord.checked_at.desc())
        .limit(RECENT_SCORE_LIMIT)
    ).all()
    if not records:
        return (1.0 if model.health_status == "healthy" else 0.0), None
    good = sum(1 for r in records if r.status == "healthy")
    passive_ms = sorted(
        r.response_ms for r in records
        if r.is_passive and r.status == "healthy" and r.response_ms is not None
    )
    median_ms = passive_ms[len(passive_ms) // 2] if passive_ms else None
    return good / len(records), median_ms


def route_score_key(model: Model, session: Session, smart: bool = False) -> tuple:
    """Composite sort key for routing candidates (lower is better).

    Order: health bucket → success rate → (param size when smart) → latency → id.
    Latency uses the real-traffic median when available and falls back to
    last_response_ms (probe value) only for models never called by traffic.
    """
    priority = HEALTH_ORDER.get(model.health_status, 3)
    rate, traffic_ms = recent_traffic_evidence(model, session)
    success_penalty = -rate
    ms = traffic_ms if traffic_ms is not None else (
        model.last_response_ms if model.last_response_ms is not None else 999999
    )
    size = -(model.param_size or 0) if smart else 0
    return (priority, success_penalty, size, ms, model.model_id)


def looks_like_vision_model(model_id: str) -> bool:
    lower = model_id.lower()
    return any(
        token in lower
        for token in (
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
        )
    )


# Models that stream their reasoning INLINE in content (z1 answers start
# with "<think>..."). Every auto:text consumer would have to strip the think
# block to get a clean answer, and long tasks burn the caller's output cap on
# thinking and return an empty body (hotspot-pipeline, 2026-09-30). Models
# with a separate reasoning/reasoning_content field do NOT belong here —
# their content contract is clean.
_INLINE_THINKING_MARKERS = ("z1", "thinking")


def looks_like_inline_thinking_model(model_id: str) -> bool:
    lower = model_id.lower()
    return any(marker in lower for marker in _INLINE_THINKING_MARKERS)


def is_generic_text_candidate(model: Model) -> bool:
    if (model.category or "text") != "text" or looks_like_vision_model(model.model_id):
        return False
    # auto:text/fast/smart promise a directly usable answer; inline-thinking
    # models break that contract. They stay callable by explicit model name.
    return not looks_like_inline_thinking_model(model.model_id)


def is_pool_eligible(model: Model, session: Session) -> bool:
    """Whether a model may appear in the free chat pool.

    Excludes non-chat categories. Free/paid status is trusted from the model's
    is_free flag, which is set authoritatively during discovery via the
    provider's free-model API (SiliconFlow charging_type=free) or the static
    whitelist as a fallback. We intentionally do NOT hard-exclude "Pro/"-prefixed
    ids here: the authoritative API sometimes marks Pro/ variants as free
    (e.g. promotional free tiers), and overriding that would be wrong.
    """
    if (model.category or "text") in NON_CHAT_CATEGORIES:
        return False
    return True
