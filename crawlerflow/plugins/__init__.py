"""Public plugin API for extending CrawlerFlow."""

from __future__ import annotations

from crawlerflow.plugins.base import (
    CrawlerFlowPlugin,
    PluginLifecycle,
    PluginRegistrationContext,
)
from crawlerflow.plugins.manager import (
    DuplicatePluginError,
    InvalidPluginError,
    PluginConfigurationError,
    PluginError,
    PluginInfo,
    PluginLifecycleError,
    PluginManager,
    PluginNotFoundError,
    discover_plugins,
)

__all__ = [
    "CrawlerFlowPlugin",
    "DuplicatePluginError",
    "InvalidPluginError",
    "PluginConfigurationError",
    "PluginError",
    "PluginInfo",
    "PluginLifecycle",
    "PluginLifecycleError",
    "PluginManager",
    "PluginNotFoundError",
    "PluginRegistrationContext",
    "discover_plugins",
]
