"""Public plugin API for extending Crawlerflow."""

from __future__ import annotations

from crawlerflow.plugins.base import (
    CrawlerflowPlugin,
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
    "CrawlerflowPlugin",
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
