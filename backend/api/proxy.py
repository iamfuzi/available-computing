import time
import json
import httpx
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional

from database import get_session
from models import Model, Channel
from api.auth import verify_token
from api.channels import _get_salt, _decrypt_key
from services.health import record_passive_health
from adapters import get_adapter

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: Optional[bool] = False


def _resolve_model(model_id: str, session: Session):
    """Find an active free model and its channel. Returns (model, channel, adapter, key)."""
    model = session.exec(
        select(Model)
        .where(Model.model_id == model_id)
        .where(Model.is_active == True)
        .where(Model.is_free == True)
    ).first()
    if not model:
        return None, None, None, None

    channel = session.get(Channel, model.channel_id)
    if not channel or not channel.enabled:
        return None, None, None, None

    adapter = get_adapter(channel.provider_type)
    key = _decrypt_key(channel.api_key_enc, session)
    return model, channel, adapter, key


def _build_openai_payload(body: ChatRequest):
    payload = {
        "model": body.model,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
        "stream": body.stream or False,
    }
    if body.max_tokens is not None:
        payload["max_tokens"] = body.max_tokens
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    return payload


async def _proxy_stream(response: httpx.Response, model_id: str, channel_id: str, key: str):
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


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    body = ChatRequest(**await request.json())
    model, channel, adapter, key = _resolve_model(body.model, session)
    if not model:
        raise HTTPException(404, detail=f"Model '{body.model}' not found or not free")

    payload = _build_openai_payload(body)
    base_url = channel.base_url or adapter.default_base_url

    # Gemini has its own chat endpoint format
    if channel.provider_type == "gemini":
        return await _proxy_gemini(model, channel, adapter, key, payload, session)

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if body.stream:
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        req = client.build_request("POST", url, json=payload, headers=headers)
        response = await client.send(req, stream=True)

        if response.status_code != 200:
            error_body = await response.aread()
            await client.aclose()
            ms = int((time.monotonic()) * 1000)
            error_code = "rate_limited" if response.status_code == 429 else "server_error"
            await record_passive_health(model.id, ms, error_code, channel.id, key)
            return JSONResponse(
                status_code=response.status_code,
                content={"error": {"message": error_body.decode(), "type": "upstream_error"}}
            )

        return StreamingResponse(
            _proxy_stream(response, model.id, channel.id, key),
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
                content={"error": {"message": r.text, "type": "upstream_error"}}
            )


async def _proxy_gemini(model, channel, adapter, key, payload, session):
    """Gemini uses :generateContent endpoint with different format."""
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model.model_id}:generateContent"

    # Convert OpenAI messages to Gemini format
    contents = []
    for msg in payload.get("messages", []):
        contents.append({"role": "user" if msg["role"] == "user" else "model", "parts": [{"text": msg["content"]}]})

    gemini_payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": payload.get("max_tokens", 1024)},
    }

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
            content={"error": {"message": r.text, "type": "upstream_error"}}
        )
