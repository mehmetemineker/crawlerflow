from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crawlerflow.browser.base import BrowserAdapter, BrowserResponse
from crawlerflow.events import EventBus, EventName, WorkflowEvent
from crawlerflow.expressions.conditions import ConditionEngine

if TYPE_CHECKING:
    from crawlerflow.engine.executor import WorkflowExecutor
    from crawlerflow.workflow.models import StepDefinition


@dataclass(slots=True)
class WorkflowContext:
    """Mutable state shared by steps during one workflow run."""

    workflow_name: str
    base_path: Path
    browser: BrowserAdapter | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    last_response: BrowserResponse | None = None
    last_html: str | None = None
    storage: dict[str, Any] = field(default_factory=dict)
    plugin_settings: dict[str, Any] = field(default_factory=dict)
    macros: dict[str, list[StepDefinition]] = field(default_factory=dict)
    condition_engine: ConditionEngine = field(default_factory=ConditionEngine)
    _scopes: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _macro_stack: list[str] = field(default_factory=list, repr=False)
    _executor: WorkflowExecutor | None = field(default=None, repr=False)
    _event_bus: EventBus | None = field(default=None, repr=False)
    _execution_path: tuple[int, ...] = field(default=(), repr=False)
    _current_step_name: str | None = field(default=None, repr=False)

    def require_browser(self) -> BrowserAdapter:
        if self.browser is None:
            raise RuntimeError("This step requires a configured browser adapter")
        return self.browser

    def fork(self) -> WorkflowContext:
        """Create isolated mutable state for a parallel loop iteration."""

        child = WorkflowContext(
            workflow_name=self.workflow_name,
            base_path=self.base_path,
            browser=self.browser,
            variables=dict(self.variables),
            outputs=dict(self.outputs),
            cookies=dict(self.cookies),
            headers=dict(self.headers),
            last_response=self.last_response,
            last_html=self.last_html,
            storage=dict(self.storage),
            plugin_settings=dict(self.plugin_settings),
            macros=self.macros,
            condition_engine=self.condition_engine,
        )
        child._scopes = [dict(scope) for scope in self._scopes]
        child._macro_stack = list(self._macro_stack)
        child._executor = self._executor
        child._event_bus = self._event_bus
        child._execution_path = self._execution_path
        child._current_step_name = self._current_step_name
        return child

    def expression_variables(self) -> dict[str, Any]:
        variables = {**self.variables, **self.outputs, "outputs": self.outputs}
        for scope in self._scopes:
            variables.update(scope)
        return variables

    @contextmanager
    def scope(self, values: dict[str, Any]) -> Iterator[None]:
        """Temporarily expose values to expressions during nested execution."""

        self._scopes.append(values)
        try:
            yield
        finally:
            self._scopes.pop()

    @contextmanager
    def loop_scope(
        self,
        *,
        item: Any,
        item_name: str,
        index: int,
        length: int,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[None]:
        """Expose consistent loop values for every loop step implementation."""

        parent_loop = self.expression_variables().get("loop")
        loop = {
            "item": item,
            item_name: item,
            "index": index + 1,
            "index0": index,
            "first": index == 0,
            "last": index == length - 1,
            "length": length,
            **(metadata or {}),
        }
        if parent_loop is not None:
            loop["parent"] = parent_loop
        with self.scope({"loop": loop}):
            yield

    @contextmanager
    def macro_scope(self, name: str, arguments: dict[str, Any]) -> Iterator[None]:
        """Expose macro arguments and reject recursive macro calls."""

        if name in self._macro_stack:
            chain = " -> ".join([*self._macro_stack, name])
            raise RuntimeError(f"Recursive macro call detected: {chain}")
        self._macro_stack.append(name)
        try:
            with self.scope({"macro": {**arguments, "name": name}}):
                yield
        finally:
            self._macro_stack.pop()

    async def execute_steps(self, steps: list[StepDefinition]) -> None:
        """Execute nested steps under the currently running step path."""

        if self._executor is None:
            raise RuntimeError("Nested step execution is not available")
        await self._executor.execute_steps(
            steps,
            self,
            parent_path=self._execution_path,
        )

    async def emit_event(self, name: EventName, payload: dict[str, Any]) -> None:
        """Publish an event associated with the currently running step."""

        if self._event_bus is None:
            return
        event_payload = {
            "path": [part + 1 for part in self._execution_path],
            **payload,
        }
        await self._event_bus.publish(
            WorkflowEvent(
                name=name,
                workflow_name=self.workflow_name,
                step_name=self._current_step_name,
                step_index=self._execution_path[-1] if self._execution_path else None,
                payload=event_payload,
            )
        )

    def resolve_path(self, path: str | Path) -> Path:
        result = Path(path)
        return result if result.is_absolute() else self.base_path / result

