"""Create browser adapters from workflow configuration."""

from __future__ import annotations

from pathlib import Path

from crawlerflow.browser.base import BrowserAdapter
from crawlerflow.workflow.models import BrowserSettings


def create_browser_adapter(
    settings: BrowserSettings,
    *,
    base_path: Path,
) -> BrowserAdapter | None:
    """Create the configured browser adapter, if browser execution is enabled."""

    if settings.engine is None:
        return None
    if settings.engine == "pydoll":
        from crawlerflow.browser.pydoll import PydollBrowserAdapter, PydollBrowserConfig

        binary_location = settings.binary_location
        if binary_location is not None and not binary_location.is_absolute():
            binary_location = base_path / binary_location
        download_directory = settings.download_directory
        if download_directory is not None and not download_directory.is_absolute():
            download_directory = base_path / download_directory
        return PydollBrowserAdapter(
            PydollBrowserConfig(
                headless=settings.headless,
                binary_location=binary_location,
                arguments=tuple(settings.arguments),
                start_timeout=settings.start_timeout,
                default_wait_timeout=settings.default_wait_timeout,
                network_idle_period=settings.network_idle_period,
                download_directory=download_directory,
            )
        )
    raise ValueError(f"Unsupported browser engine: {settings.engine}")
