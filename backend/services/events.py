import asyncio
import json
import logging
from typing import Callable

_listeners: list[Callable] = []
logger = logging.getLogger(__name__)


def subscribe(callback: Callable):
    _listeners.append(callback)


def unsubscribe(callback: Callable):
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
            logger.exception("Event listener failed")
