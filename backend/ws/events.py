from fastapi import APIRouter, WebSocket, WebSocketDisconnect
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


@router.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            # Keep alive — client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.discard(websocket)
