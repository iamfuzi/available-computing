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


def _resolve_model(model_id: str, session: Session):
    """Find an active free healthy model and its channel. Returns (model, channel, adapter, key)."""
    candidates = session.exec(
        select(Model)
        .where(Model.model_id == model_id)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
        .where(Model.health_status != "down")
    ).all()
    # Exclude non-chat models (image/video generation, embedding)
    candidates = [m for m in candidates if (m.category or "text") not in _NON_CHAT_CATEGORIES]

    candidates.sort(key=_health_sort_key)

    for model in candidates:
        result = _try_bind_model(model, session)
        if result:
            return model, result[0], result[1], result[2]

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


_NON_CHAT_CATEGORIES = {"image", "video", "embedding", "rerank"}


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
            return _make_openai_error(404, f"Model '{body.model}' not found or not available", "invalid_request_error", "model")

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
            error_code = "rate_limited" if response.status_code == 429 else "server_error"
            await record_passive_health(model.id, ms, error_code, channel.id, key)
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

        error_code = None
        if r.status_code == 200:
            await record_passive_health(model.id, ms, None, channel.id, key)
            return JSONResponse(content=r.json(), status_code=200)
        else:
            error_code = "rate_limited" if r.status_code == 429 else "server_error"
            await record_passive_health(model.id, ms, error_code, channel.id, key)
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
        error_code = "rate_limited" if r.status_code == 429 else "server_error"
        await record_passive_health(model.id, ms, error_code, channel.id, key)
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
        error_code = "rate_limited" if r.status_code == 429 else "server_error"
        await record_passive_health(model.id, ms, error_code, channel.id, key)

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
