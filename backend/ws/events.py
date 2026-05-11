import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import jwt, JWTError
from config import JWT_SECRET, JWT_ALGORITHM
from services import events

router = APIRouter()
_connections: set[WebSocket] = set()


async def _send(message: str):
    dead = set()
    for ws in list(_connections):
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


events.subscribe(_send)


async def _cleanup_dead_connections():
    """Periodically ping all connections and remove dead ones."""
    while True:
        await asyncio.sleep(60)
        if not _connections:
            continue
        dead = set()
        for ws in list(_connections):
            try:
                await ws.send_json({"event": "ping"})
            except Exception:
                dead.add(ws)
        _connections.difference_update(dead)


async def start_cleanup_task():
    task = asyncio.create_task(_cleanup_dead_connections())
    return task


@router.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket, token: str = Query()):
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.discard(websocket)
