import asyncio
import json
from typing import Callable

_listeners: list[Callable] = []


def subscribe(callback: Callable):
    _listeners.append(callback)


def unsubscribe(callback: Callable):
    _listeners.discard(callback) if hasattr(_listeners, "discard") else None
    if callback in _listeners:
        _listeners.remove(callback)


async def broadcast(event: str, data: dict):
    message = json.dumps({"event": event, "data": data})
    for callback in list(_listeners):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(message)
            else:
                callback(message)
        except Exception:
            pass
