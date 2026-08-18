from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from inspect import isawaitable

from crawlerflow.events.models import EventName, WorkflowEvent

type EventHandler = Callable[[WorkflowEvent], Awaitable[None] | None]


class EventBus:
    """Small in-process event bus for core and plugin subscribers."""

    def __init__(self) -> None:
        self._handlers: dict[EventName, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: EventName, handler: EventHandler) -> None:
        self._handlers[event_name].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        for event_name in EventName:
            self.subscribe(event_name, handler)

    def unsubscribe(self, event_name: EventName, handler: EventHandler) -> None:
        handlers = self._handlers[event_name]
        if handler in handlers:
            handlers.remove(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        for event_name in EventName:
            self.unsubscribe(event_name, handler)

    async def publish(self, event: WorkflowEvent) -> None:
        for handler in self._handlers[event.name]:
            result = handler(event)
            if isawaitable(result):
                await result
