from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from crawlerflow.engine.registry import StepRegistry
from crawlerflow.events import EventBus
from crawlerflow.expressions import ExpressionEngine

if TYPE_CHECKING:
    from crawlerflow.engine.context import WorkflowContext


@dataclass(slots=True, frozen=True)
class PluginRegistrationContext:
    """Core extension points exposed while a plugin is registered."""

    registry: StepRegistry
    expression_engine: ExpressionEngine
    event_bus: EventBus


class CrawlerflowPlugin(Protocol):
    """Contract implemented by in-process Crawlerflow plugins."""

    name: str

    def register(self, context: PluginRegistrationContext) -> None: ...


class PluginLifecycle(Protocol):
    """Optional synchronous or asynchronous plugin runtime hooks."""

    def startup(self, context: WorkflowContext) -> Awaitable[None] | None: ...

    def shutdown(self, context: WorkflowContext) -> Awaitable[None] | None: ...
