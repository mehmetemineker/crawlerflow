from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from crawlerflow.browser import BrowserAdapter, create_browser_adapter
from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.executor import WorkflowExecutor
from crawlerflow.engine.registry import StepRegistry, default_registry
from crawlerflow.events import ConsoleEventLogger, EventBus, JsonEventLogger
from crawlerflow.expressions import ExpressionEngine
from crawlerflow.plugins import CrawlerflowPlugin, PluginManager
from crawlerflow.steps import load_builtin_steps
from crawlerflow.workflow import WorkflowDocument, WorkflowLoader
from crawlerflow.workflow.models import StepDefinition


class WorkflowRunner:
    """High-level facade for validating and executing workflow files."""

    def __init__(
        self,
        *,
        browser: BrowserAdapter | None = None,
        registry: StepRegistry | None = None,
        expression_engine: ExpressionEngine | None = None,
        event_bus: EventBus | None = None,
        plugins: Iterable[CrawlerflowPlugin] = (),
    ) -> None:
        self.browser = browser
        self.expression_engine = expression_engine or ExpressionEngine()
        self.event_bus = event_bus or EventBus()
        load_builtin_steps()
        self.registry = registry or StepRegistry()
        self.registry.include(default_registry)
        self.plugin_manager = PluginManager(
            self.registry,
            self.expression_engine,
            self.event_bus,
        )
        for plugin in plugins:
            self.plugin_manager.register(plugin)
        self._loader = WorkflowLoader()

    def load(self, path: str | Path) -> WorkflowDocument:
        workflow = self._loader.load(path)
        self._prepare_plugins(workflow)
        self.validate(workflow)
        return workflow

    def _prepare_plugins(self, workflow: WorkflowDocument) -> dict[str, object]:
        self.plugin_manager.load_configured([plugin.name for plugin in workflow.plugins])
        return self.plugin_manager.validate_settings(
            {plugin.name: plugin.settings for plugin in workflow.plugins}
        )

    def validate(self, workflow: WorkflowDocument) -> None:
        for macro_steps in workflow.macros.values():
            self._validate_steps(macro_steps, workflow.macros)
        self._validate_steps(workflow.steps, workflow.macros)

    def _validate_steps(
        self,
        steps: list[StepDefinition],
        macros: dict[str, list[StepDefinition]],
    ) -> None:
        for definition in steps:
            if self._contains_expression(definition.config):
                self.registry.validate_structure(definition.name, definition.config)
            else:
                self.registry.create(definition.name, definition.config)
            for field_name in self.registry.nested_step_fields(definition.name):
                nested_steps = definition.config.get(field_name, [])
                if not isinstance(nested_steps, list):
                    continue
                parsed_steps = [StepDefinition.model_validate(step) for step in nested_steps]
                self._validate_steps(parsed_steps, macros)
            if definition.name == "run_macro":
                macro_name = definition.config.get("name")
                if (
                    isinstance(macro_name, str)
                    and not self._contains_expression(macro_name)
                    and macro_name not in macros
                ):
                    raise ValueError(f"Unknown macro: {macro_name}")

    @classmethod
    def _contains_expression(cls, value: object) -> bool:
        if isinstance(value, str):
            return "{{" in value and "}}" in value
        if isinstance(value, dict):
            return any(cls._contains_expression(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(cls._contains_expression(item) for item in value)
        return False

    async def run(self, path: str | Path) -> WorkflowContext:
        workflow_path = Path(path).resolve()
        workflow = self.load(workflow_path)
        active_browser = self.browser or create_browser_adapter(
            workflow.browser,
            base_path=workflow_path.parent,
        )
        event_loggers = self._create_event_loggers(workflow, workflow_path.parent)
        for event_logger in event_loggers:
            event_logger.prepare()
            event_logger.attach(self.event_bus)
        context = WorkflowContext(
            workflow_name=workflow.workflow.name,
            base_path=workflow_path.parent,
            browser=active_browser,
            variables=self._runtime_variables(workflow),
            plugin_settings=self._prepare_plugins(workflow),
            macros=workflow.macros,
        )
        context._event_bus = self.event_bus
        executor = WorkflowExecutor(self.registry, self.expression_engine, self.event_bus)
        started_plugins: tuple[CrawlerflowPlugin, ...] = ()
        primary_error: BaseException | None = None
        try:
            started_plugins = await self.plugin_manager.startup(context)
            return await executor.execute(workflow, context)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                await self.plugin_manager.shutdown(context, started_plugins)
            except Exception as error:
                if primary_error is None:
                    raise
                primary_error.add_note(str(error))
            finally:
                for event_logger in reversed(event_loggers):
                    event_logger.detach(self.event_bus)
                if active_browser is not None:
                    await active_browser.close()

    @staticmethod
    def _runtime_variables(workflow: WorkflowDocument) -> dict[str, object]:
        started_at = datetime.now().astimezone()
        return {
            "now": started_at,
            "today": started_at.date(),
            **workflow.variables,
        }

    @staticmethod
    def _create_event_loggers(
        workflow: WorkflowDocument,
        base_path: Path,
    ) -> list[ConsoleEventLogger | JsonEventLogger]:
        settings = workflow.logging
        if not settings.enabled:
            return []

        event_loggers: list[ConsoleEventLogger | JsonEventLogger] = []
        if settings.console:
            event_loggers.append(ConsoleEventLogger())
        if settings.path is not None:
            path = settings.path if settings.path.is_absolute() else base_path / settings.path
            event_loggers.append(
                JsonEventLogger(
                    path,
                    mode=settings.mode,
                    include_payload=settings.include_payload,
                )
            )
        return event_loggers
