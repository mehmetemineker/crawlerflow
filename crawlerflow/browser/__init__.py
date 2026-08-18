"""Browser adapter contracts and implementations."""

from __future__ import annotations

from crawlerflow.browser.base import BrowserAdapter, BrowserResponse
from crawlerflow.browser.factory import create_browser_adapter

__all__ = ["BrowserAdapter", "BrowserResponse", "create_browser_adapter"]
