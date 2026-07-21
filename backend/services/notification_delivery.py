"""Extensible notification delivery boundary.

Station notifications are persisted by ``services.notifications``. External
delivery is deliberately behind this tiny interface so a webhook, mail, or
chat-bot sink can be added without coupling detection logic to one vendor.
"""

from __future__ import annotations

from typing import Protocol


class NotificationSink(Protocol):
    async def send(self, payload: dict) -> None: ...


class NotificationDispatcher:
    def __init__(self) -> None:
        self._sinks: list[NotificationSink] = []

    def register(self, sink: NotificationSink) -> None:
        self._sinks.append(sink)

    async def dispatch(self, payload: dict) -> None:
        for sink in list(self._sinks):
            await sink.send(payload)


dispatcher = NotificationDispatcher()
