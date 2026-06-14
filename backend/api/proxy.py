import re
import json
import time
import httpx
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlmodel import Session, select
from pydantic import BaseModel, ConfigDict
from typing import Optional

from database import get_session
from models import Model, Channel
from api.auth import verify_token_or_apikey
from api.channels import _decrypt_key
from services.health import record_passive_health
from adapters import get_adapter

router = APIRouter()

_VALID_CATEGORIES = {"text", "vision", "code", "embedding", "image", "video"}
_AUTO_RE = re.compile(r"^auto:(" + "|".join(_VALID_CATEGORIES) + r")$")

# Health priority: healthy (0) > slow (1) > unknown (2) > down (3)
_HEALTH_ORDER = {"healthy": 0, "slow": 1, "unknown": 2}

# Categories that are not chat-completion targets (excluded from model resolution)
_NON_CHAT_CATEGORIES = {"image", "video", "embedding", "rerank"}

_proxy_requests: dict[str, list[float]] = {}
PROXY_RATE_LIMIT = 60
PROXY_RATE_WINDOW = 60


def _check_proxy_rate_limit(ip: str):
    now = time.time()
    attempts = _proxy_requests.get(ip, [])
    attempts = [t for t in attempts if now - t < PROXY_RATE_WINDOW]
    _proxy_requests[ip] = attempts
    if len(attempts) >= PROXY_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _proxy_requests.setdefault(ip, []).append(now)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str
    messages: list[ChatMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = False
    stop: Optional[list[str]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None


def _health_sort_key(model: Model) -> tuple:
    """Sort key: (health_priority, response_ms). Lower is better."""
    priority = _HEALTH_ORDER.get(model.health_status, 3)
    ms = model.last_response_ms if model.last_response_ms is not None else 999999
    return (priority, ms)


def _try_bind_model(model: Model, session: Session):
    """Try to bind a model to its channel, adapter, and decrypted key."""
    channel = session.get(Channel, model.channel_id)
    if not channel or not channel.enabled:
        return None
    adapter = get_adapter(channel.provider_type)
    key = _decrypt_key(channel.api_key_enc, session)
    return channel, adapter, key


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


def _chat_candidates(session: Session):
    """All active, free, non-down chat models. Caller further filters/sorts."""
    rows = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status != "down")
    ).all()
    return [m for m in rows if (m.category or "text") not in _NON_CHAT_CATEGORIES]


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
        return (priority, ms)
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


def _resolve_model(model_id: str, session: Session):
    """Find an active free healthy model and its channel.

    Matching is tolerant, in three tiers:
      1. exact ``model_id`` match
      2. case-insensitive match
      3. normalized match (lowercased, org-prefix dropped, variant suffix stripped)

    Returns (model, channel, adapter, key) or (None, None, None, None).
    """
    candidates = _chat_candidates(session)

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


def _resolve_auto_model(category: str, session: Session):
    """Auto-select the best available model for a given category."""
    candidates = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status != "down")
        .where(Model.category == category)
    ).all()

    candidates.sort(key=_health_sort_key)

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


async def _proxy_stream(response: httpx.Response, client: httpx.AsyncClient, model_id: str, channel_id: str, key: str):
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


def _make_openai_error(status_code: int, message: str, error_type: str = "invalid_request_error", param: str | None = None):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "param": param, "code": None}},
    )


@router.get("/models")
def list_openai_models(
    session: Session = Depends(get_session),
    _=Depends(verify_token_or_apikey),
):
    """OpenAI-compatible model listing."""
    models = session.exec(
        select(Model)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
    ).all()

    data = []
    for m in models:
        if (m.category or "text") in _NON_CHAT_CATEGORIES:
            continue
        data.append({
            "id": m.model_id,
            "object": "model",
            "created": 0,
            "owned_by": "available-computing",
        })
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatRequest,
    session: Session = Depends(get_session),
    _=Depends(verify_token_or_apikey),
):
    ip = request.client.host if request.client else "unknown"
    _check_proxy_rate_limit(ip)

    # Check for auto:category routing
    auto_match = _AUTO_RE.match(body.model)
    if auto_match:
        category = auto_match.group(1)
        model, channel, adapter, key = _resolve_auto_model(category, session)
        if not model:
            return _make_openai_error(404, f"No available models for category '{category}'", "invalid_request_error", "model")
        body.model = model.model_id
    else:
        model, channel, adapter, key = _resolve_model(body.model, session)
        if not model:
            suggestions = _suggest_models(body.model, _chat_candidates(session))
            hint = (
                f" Did you mean: {', '.join(suggestions)}?"
                if suggestions
                else " Call GET /v1/models to list available ids."
            )
            return _make_openai_error(
                404,
                f"Model '{body.model}' not found or not available.{hint}",
                "invalid_request_error",
                "model",
            )

    payload = _build_openai_payload(body)
    base_url = channel.base_url or adapter.default_base_url

    # Gemini has its own chat endpoint format
    if channel.provider_type == "gemini":
        if body.stream:
            return await _proxy_gemini_stream(model, channel, adapter, key, payload, session)
        return await _proxy_gemini(model, channel, adapter, key, payload, session)

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if body.stream:
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        req = client.build_request("POST", url, json=payload, headers=headers)
        start = time.monotonic()
        response = await client.send(req, stream=True)

        if response.status_code != 200:
            error_body = await response.aread()
            await client.aclose()
            ms = int((time.monotonic() - start) * 1000)
            # Only server-side failures (5xx) should mark the model unhealthy.
            # 4xx are caller-side problems (bad params, auth, etc.) and don't
            # mean the model is down; 429 means rate-limited, not down.
            if response.status_code >= 500:
                await record_passive_health(model.id, ms, "server_error", channel.id, key)
            return JSONResponse(
                status_code=response.status_code,
                content={"error": {"message": f"Upstream returned {response.status_code}", "type": "upstream_error"}},
            )

        return StreamingResponse(
            _proxy_stream(response, client, model.id, channel.id, key),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )
    else:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers=headers)
        ms = int((time.monotonic() - start) * 1000)

        if r.status_code == 200:
            await record_passive_health(model.id, ms, None, channel.id, key)
            return JSONResponse(content=r.json(), status_code=200)
        else:
            # See note above: only 5xx marks the model unhealthy.
            if r.status_code >= 500:
                await record_passive_health(model.id, ms, "server_error", channel.id, key)
            return JSONResponse(
                status_code=r.status_code,
                content={"error": {"message": f"Upstream returned {r.status_code}", "type": "upstream_error"}},
            )


async def _proxy_gemini(model, channel, adapter, key, payload, session):
    """Gemini uses :generateContent endpoint with different format."""
    base_url = channel.base_url or "https://generativelanguage.googleapis.com/v1beta"
    gemini_url = f"{base_url}/models/{model.model_id}:generateContent"

    # Convert OpenAI messages to Gemini format
    system_instruction = None
    contents = []
    for msg in payload.get("messages", []):
        role = msg["role"]
        if role == "system":
            system_instruction = msg["content"]
        else:
            contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": msg["content"]}]})

    gemini_payload: dict = {"contents": contents}
    if system_instruction:
        gemini_payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    gen_config: dict = {}
    if payload.get("max_tokens"):
        gen_config["maxOutputTokens"] = payload["max_tokens"]
    if payload.get("temperature") is not None:
        gen_config["temperature"] = payload["temperature"]
    if gen_config:
        gemini_payload["generationConfig"] = gen_config

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(gemini_url, params={"key": key}, json=gemini_payload)
    ms = int((time.monotonic() - start) * 1000)

    if r.status_code == 200:
        await record_passive_health(model.id, ms, None, channel.id, key)
        # Convert Gemini response to OpenAI format
        gemini_resp = r.json()
        text = ""
        for cand in gemini_resp.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                text += part.get("text", "")
        openai_resp = {
            "id": f"chatcmpl-{model.id[:8]}",
            "object": "chat.completion",
            "model": model.model_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        return JSONResponse(content=openai_resp, status_code=200)
    else:
        # Only 5xx means the upstream is actually unhealthy; 4xx/429 are caller-side.
        if r.status_code >= 500:
            await record_passive_health(model.id, ms, "server_error", channel.id, key)
        return JSONResponse(
            status_code=r.status_code,
            content={"error": {"message": f"Upstream returned {r.status_code}", "type": "upstream_error"}},
        )


async def _proxy_gemini_stream(model, channel, adapter, key, payload, session):
    """Stream Gemini responses in OpenAI SSE format using non-streaming generateContent."""
    base_url = channel.base_url or "https://generativelanguage.googleapis.com/v1beta"

    system_instruction = None
    contents = []
    for msg in payload.get("messages", []):
        role = msg["role"]
        if role == "system":
            system_instruction = msg["content"]
        else:
            contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": msg["content"]}]})

    gemini_payload: dict = {"contents": contents}
    if system_instruction:
        gemini_payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    gen_config: dict = {}
    if payload.get("max_tokens"):
        gen_config["maxOutputTokens"] = payload["max_tokens"]
    if payload.get("temperature") is not None:
        gen_config["temperature"] = payload["temperature"]
    if gen_config:
        gemini_payload["generationConfig"] = gen_config

    gemini_url = f"{base_url}/models/{model.model_id}:generateContent"
    chunk_id = f"chatcmpl-{model.id[:8]}"
    start = time.monotonic()

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(gemini_url, params={"key": key}, json=gemini_payload)

    ms = int((time.monotonic() - start) * 1000)

    if r.status_code != 200:
        # Only 5xx means the upstream is actually unhealthy; 4xx/429 are caller-side.
        if r.status_code >= 500:
            await record_passive_health(model.id, ms, "server_error", channel.id, key)

        async def error_gen():
            yield f"data: {json.dumps({'error': {'message': f'Upstream returned {r.status_code}', 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(error_gen(), media_type="text/event-stream")

    await record_passive_health(model.id, ms, None, channel.id, key)

    text = ""
    for cand in r.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            text += part.get("text", "")

    async def generate():
        if text:
            sse = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "model": model.model_id,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(sse)}\n\n"
            sse["choices"][0]["delta"] = {"content": text}
            yield f"data: {json.dumps(sse)}\n\n"
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'model': model.model_id, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
