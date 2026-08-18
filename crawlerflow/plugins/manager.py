from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from inspect import isawaitable
from typing import Any

from pydantic import BaseModel, ValidationError

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import StepRegistry
from crawlerflow.events import EventBus
from crawlerflow.expressions import ExpressionEngine
from crawlerflow.plugins.base import CrawlerflowPlugin, PluginRegistrationContext

ENTRY_POINT_GROUP = "crawlerflow.plugins"


@dataclass(slots=True, frozen=True)
class PluginInfo:
    """Installed plugin metadata discovered without importing plugin code."""

    name: str
    target: str
    distribution: str | None = None


def discover_plugins() -> tuple[PluginInfo, ...]:
    """Return installed Crawlerflow entry points without loading them."""

    plugins = []
    for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP):
        distribution = getattr(entry_point, "dist", None)
        distribution_name = getattr(distribution, "name", None)
        plugins.append(
            PluginInfo(
                name=entry_point.name,
                target=entry_point.value,
                distribution=distribution_name,
            )
        )
    return tuple(sorted(plugins, key=lambda plugin: (plugin.name, plugin.target)))


class PluginError(ValueError):
    """Base error raised while loading or registering a plugin."""


class PluginNotFoundError(PluginError):
    """Raised when a configured plugin has no installed entry point."""


class DuplicatePluginError(PluginError):
    """Raised when two plugins use the same name."""


class InvalidPluginError(PluginError):
    """Raised when an entry point does not satisfy the plugin contract."""


class PluginConfigurationError(PluginError):
    """Raised when workflow settings are invalid for a plugin."""


class PluginLifecycleError(PluginError):
    """Raised when one or more plugin runtime hooks fail."""

    def __init__(self, failures: list[tuple[str, str, Exception]]) -> None:
        self.failures = tuple(failures)
        details = "; ".join(
            f"{name}.{phase}: {error}" for name, phase, error in failures
        )
        super().__init__(f"Plugin lifecycle failed: {details}")


class PluginManager:
    """Register explicit plugins and load opted-in package entry points."""

    def __init__(
        self,
        registry: StepRegistry,
        expression_engine: ExpressionEngine,
        event_bus: EventBus,
    ) -> None:
        self._context = PluginRegistrationContext(registry, expression_engine, event_bus)
        self._plugins: dict[str, CrawlerflowPlugin] = {}

    @property
    def loaded_names(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def register(self, plugin: CrawlerflowPlugin) -> None:
        name = getattr(plugin, "name", None)
        register = getattr(plugin, "register", None)
        if not isinstance(name, str) or not name or not name.replace("-", "_").isidentifier():
            raise InvalidPluginError("Plugin must define a valid non-empty name")
        if not callable(register):
            raise InvalidPluginError(f"Plugin '{name}' must define register(context)")
        if name in self._plugins:
            raise DuplicatePluginError(f"Plugin is already registered: {name}")

        register(self._context)
        self._plugins[name] = plugin

    def validate_settings(
        self,
        configured: dict[str, dict[str, Any]],
    ) -> dict[str, BaseModel | dict[str, Any]]:
        """Validate settings for every loaded plugin without running plugin code."""

        validated: dict[str, BaseModel | dict[str, Any]] = {}
        for name, plugin in self._plugins.items():
            settings = configured.get(name, {})
            settings_model = getattr(plugin, "settings_model", None)
            if settings_model is None:
                if settings:
                    raise PluginConfigurationError(
                        f"Plugin '{name}' does not declare a settings model"
                    )
                validated[name] = {}
                continue
            if not isinstance(settings_model, type) or not issubclass(settings_model, BaseModel):
                raise InvalidPluginError(
                    f"Plugin '{name}' settings_model must be a Pydantic BaseModel"
                )
            try:
                validated[name] = settings_model.model_validate(settings)
            except ValidationError as error:
                raise PluginConfigurationError(
                    f"Invalid settings for plugin '{name}': {error}"
                ) from error
        return validated

    async def startup(self, context: WorkflowContext) -> tuple[CrawlerflowPlugin, ...]:
        """Start plugins in registration order and return successfully started plugins."""

        started: list[CrawlerflowPlugin] = []
        for name, plugin in self._plugins.items():
            try:
                await self._call_hook(plugin, "startup", context)
            except Exception as error:
                failures = [(name, "startup", error)]
                failures.extend(await self._shutdown_plugins(started, context))
                raise PluginLifecycleError(failures) from error
            started.append(plugin)
        return tuple(started)

    async def shutdown(
        self,
        context: WorkflowContext,
        started_plugins: tuple[CrawlerflowPlugin, ...],
    ) -> None:
        """Stop successfully started plugins in reverse registration order."""

        failures = await self._shutdown_plugins(started_plugins, context)
        if failures:
            raise PluginLifecycleError(failures)

    def load_configured(self, names: list[str]) -> None:
        if not names:
            return
        entry_points: dict[str, list[Any]] = {}
        for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP):
            entry_points.setdefault(entry_point.name, []).append(entry_point)
        for name in names:
            if name in self._plugins:
                continue
            matches = entry_points.get(name, [])
            if not matches:
                raise PluginNotFoundError(f"Configured plugin is not installed: {name}")
            if len(matches) > 1:
                raise DuplicatePluginError(f"Multiple installed plugins use the name: {name}")
            entry_point = matches[0]

            try:
                target: Any = entry_point.load()
                plugin = target() if isinstance(target, type) else target
                if getattr(plugin, "name", None) != name:
                    raise InvalidPluginError(
                        f"Entry point '{name}' loaded plugin '{getattr(plugin, 'name', None)}'"
                    )
                self.register(plugin)
            except PluginError:
                raise
            except Exception as error:
                raise InvalidPluginError(f"Could not load plugin '{name}': {error}") from error

    async def _shutdown_plugins(
        self,
        plugins: tuple[CrawlerflowPlugin, ...] | list[CrawlerflowPlugin],
        context: WorkflowContext,
    ) -> list[tuple[str, str, Exception]]:
        failures = []
        for plugin in reversed(plugins):
            try:
                await self._call_hook(plugin, "shutdown", context)
            except Exception as error:
                failures.append((plugin.name, "shutdown", error))
        return failures

    @staticmethod
    async def _call_hook(
        plugin: CrawlerflowPlugin,
        hook_name: str,
        context: WorkflowContext,
    ) -> None:
        hook = getattr(plugin, hook_name, None)
        if not callable(hook):
            return
        result = hook(context)
        if isawaitable(result):
            await result
