from __future__ import annotations

import asyncio
import time
from typing import Any

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import StepRegistry
from crawlerflow.events import EventBus, EventName, WorkflowEvent
from crawlerflow.expressions import ExpressionEngine
from crawlerflow.workflow.models import RetryPolicy, StepDefinition, WorkflowDocument


class WorkflowExecutionError(RuntimeError):
    """Wrap a step failure with workflow location information."""

    def __init__(self, path: tuple[int, ...], step_name: str, error: Exception) -> None:
        self.path = path
        self.step_name = step_name
        self.original_error = error
        location = ".".join(str(index + 1) for index in path)
        super().__init__(f"Step {location} ('{step_name}') failed: {error}")


class _RetryExhausted(Exception):
    def __init__(self, error: Exception, attempts: int) -> None:
        self.error = error
        self.attempts = attempts
        super().__init__(str(error))


class WorkflowExecutor:
    """Resolve and execute workflow steps in declaration order."""

    def __init__(
        self,
        registry: StepRegistry,
        expression_engine: ExpressionEngine,
        event_bus: EventBus,
    ) -> None:
        self._registry = registry
        self._expression_engine = expression_engine
        self._event_bus = event_bus

    async def execute(
        self,
        workflow: WorkflowDocument,
        context: WorkflowContext,
    ) -> WorkflowContext:
        context._executor = self
        context._event_bus = self._event_bus
        await self._emit(EventName.WORKFLOW_STARTED, context)
        try:
            await self.execute_steps(workflow.steps, context)
        except Exception as error:
            await self._emit(
                EventName.WORKFLOW_FINISHED,
                context,
                payload={"status": "failed", "error": str(error)},
            )
            raise
        await self._emit(
            EventName.WORKFLOW_FINISHED,
            context,
            payload={"status": "succeeded"},
        )
        return context

    async def execute_steps(
        self,
        steps: list[StepDefinition],
        context: WorkflowContext,
        *,
        parent_path: tuple[int, ...] = (),
    ) -> None:
        """Execute a sequence of top-level or nested step definitions."""

        for index, definition in enumerate(steps):
            path = (*parent_path, index)
            previous_path = context._execution_path
            previous_step_name = context._current_step_name
            context._execution_path = path
            context._current_step_name = definition.name
            await self._emit(
                EventName.STEP_STARTED,
                context,
                definition.name,
                index,
                {
                    "path": [part + 1 for part in path],
                    "max_attempts": definition.retry.attempts if definition.retry else 1,
                },
            )
            started_at = time.monotonic()
            continued = False
            attempts_used = 1
            try:
                resolved_config = self._resolve_step_config(
                    definition.name,
                    definition.config,
                    context.expression_variables(),
                )
                workflow_step = self._registry.create(definition.name, resolved_config)
                result, attempts_used = await self._execute_with_retry(
                    definition,
                    workflow_step,
                    context,
                    path,
                    index,
                )
                if result is not None:
                    context.storage["last_step_result"] = result
            except Exception as error:
                if isinstance(error, _RetryExhausted):
                    attempts_used = error.attempts
                    error = error.error
                error_details = self._error_details(error, definition, path)
                context.storage["last_error"] = error_details
                if definition.save_error_as is not None:
                    context.outputs[definition.save_error_as] = error_details
                await self._emit(
                    EventName.STEP_FAILED,
                    context,
                    definition.name,
                    index,
                    {
                        **error_details,
                        "path": [part + 1 for part in path],
                        "on_error": definition.on_error,
                        "duration_ms": self._duration_ms(started_at),
                    },
                )
                if definition.on_error == "continue":
                    continued = True
                elif isinstance(error, WorkflowExecutionError):
                    raise
                else:
                    raise WorkflowExecutionError(path, definition.name, error) from error
            finally:
                context._execution_path = previous_path
                context._current_step_name = previous_step_name
            await self._emit(
                EventName.STEP_FINISHED,
                context,
                definition.name,
                index,
                {
                    "path": [part + 1 for part in path],
                    "status": "continued" if continued else "succeeded",
                    "attempts": attempts_used,
                    "duration_ms": self._duration_ms(started_at),
                },
            )

    async def _execute_with_retry(
        self,
        definition: StepDefinition,
        workflow_step: Any,
        context: WorkflowContext,
        path: tuple[int, ...],
        step_index: int,
    ) -> tuple[Any, int]:
        policy = definition.retry or RetryPolicy(attempts=1)
        try:
            return await workflow_step.execute(context), 1
        except Exception as error:
            last_error = error

        for attempt in range(2, policy.attempts + 1):
            delay = policy.delay_for(attempt)
            payload = {
                "path": [part + 1 for part in path],
                "attempt": attempt,
                "max_attempts": policy.attempts,
                "delay": delay,
                "previous_error": str(last_error),
            }
            await self._emit(
                EventName.RETRY_STARTED,
                context,
                definition.name,
                step_index,
                payload,
            )
            if delay:
                await asyncio.sleep(delay)
            try:
                result = await workflow_step.execute(context)
            except Exception as error:
                last_error = error
                await self._emit(
                    EventName.RETRY_FINISHED,
                    context,
                    definition.name,
                    step_index,
                    {**payload, "succeeded": False, "error": str(error)},
                )
            else:
                await self._emit(
                    EventName.RETRY_FINISHED,
                    context,
                    definition.name,
                    step_index,
                    {**payload, "succeeded": True},
                )
                return result, attempt
        if policy.attempts == 1:
            raise last_error
        raise _RetryExhausted(last_error, policy.attempts) from last_error

    @staticmethod
    def _error_details(
        error: Exception,
        definition: StepDefinition,
        path: tuple[int, ...],
    ) -> dict[str, Any]:
        if isinstance(error, WorkflowExecutionError):
            error_path = error.path
            step_name = error.step_name
            error_type = type(error.original_error).__name__
        else:
            error_path = path
            step_name = definition.name
            error_type = type(error).__name__
        return {
            "error": str(error),
            "error_type": error_type,
            "failed_step": step_name,
            "failed_path": [part + 1 for part in error_path],
        }

    @staticmethod
    def _duration_ms(started_at: float) -> float:
        return round((time.monotonic() - started_at) * 1000, 3)

    def _resolve_step_config(
        self,
        step_name: str,
        config: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        deferred_fields = self._registry.deferred_fields(step_name)
        return {
            key: (
                value
                if key in deferred_fields
                else (
                    self._expression_engine.resolve_path(value, variables)
                    if key == "path"
                    else self._expression_engine.resolve(value, variables)
                )
            )
            for key, value in config.items()
        }

    async def _emit(
        self,
        name: EventName,
        context: WorkflowContext,
        step_name: str | None = None,
        step_index: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._event_bus.publish(
            WorkflowEvent(
                name=name,
                workflow_name=context.workflow_name,
                step_name=step_name,
                step_index=step_index,
                payload=payload or {},
            )
        )

