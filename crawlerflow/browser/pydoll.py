"""Pydoll implementation of the browser adapter contract."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crawlerflow.browser.base import BrowserAdapter, BrowserResponse

logger = logging.getLogger(__name__)


class PydollAdapterError(RuntimeError):
    """Base error raised by the Pydoll adapter."""


class PydollNotInstalledError(PydollAdapterError):
    """Raised when the optional Pydoll dependency is unavailable."""


class NetworkIdleTimeout(PydollAdapterError, TimeoutError):
    """Raised when browser network activity does not become idle in time."""


@dataclass(slots=True, frozen=True)
class PydollBrowserConfig:
    """Runtime options used to launch a Pydoll Chromium browser."""

    headless: bool = True
    binary_location: Path | None = None
    arguments: tuple[str, ...] = ()
    start_timeout: int = 10
    default_wait_timeout: float = 10
    network_idle_period: float = 0.5
    download_directory: Path | None = None


class PydollBrowserAdapter(BrowserAdapter):
    """Drive Chromium through Pydoll while exposing backend-neutral operations."""

    def __init__(
        self,
        config: PydollBrowserConfig | None = None,
        *,
        browser: Any = None,
        tab: Any = None,
    ) -> None:
        self.config = config or PydollBrowserConfig()
        self._browser = browser
        self._tab = tab
        self._owns_browser = browser is None
        self._start_lock = asyncio.Lock()
        self._network_tracking_enabled = False
        self._network_callback_ids: list[int] = []
        self._inflight_requests: set[str] = set()
        self._last_network_activity = time.monotonic()

    async def goto(self, url: str) -> None:
        tab = await self._get_tab()
        await tab.go_to(url)

    async def click(self, selector: str) -> None:
        element = await self._query(selector)
        await element.click()

    async def fill(self, selector: str, value: str) -> None:
        element = await self._query(selector)
        await element.clear()
        await element.insert_text(value)

    async def select(self, selector: str, value: str) -> None:
        element = await self._query(selector)
        escaped_value = self._json_string(value)
        result = await element.execute_script(
            "if (!(this instanceof HTMLSelectElement)) { "
            "throw new Error('Selected element is not a <select>'); "
            "} "
            f"this.value = {escaped_value}; "
            "this.dispatchEvent(new Event('input', { bubbles: true })); "
            "this.dispatchEvent(new Event('change', { bubbles: true })); "
            "return this.value;",
            return_by_value=True,
        )
        selected_value = self._unwrap_script_result(result)
        if selected_value != value:
            raise PydollAdapterError(
                f"Option value '{value}' was not found for selector '{selector}'"
            )

    async def wait(self, selector: str, timeout_seconds: float | None = None) -> None:
        await self._query(selector, timeout_seconds=timeout_seconds)

    async def wait_network(self, timeout_seconds: float | None = None) -> None:
        await self._get_tab()
        timeout = timeout_seconds or self.config.default_wait_timeout
        deadline = time.monotonic() + timeout

        while True:
            now = time.monotonic()
            idle_for = now - self._last_network_activity
            if not self._inflight_requests and idle_for >= self.config.network_idle_period:
                return
            if now >= deadline:
                raise NetworkIdleTimeout(
                    f"Network did not become idle within {timeout:g} seconds "
                    f"({len(self._inflight_requests)} request(s) still active)"
                )
            await asyncio.sleep(min(0.05, deadline - now))

    async def html(self) -> str:
        tab = await self._get_tab()
        return await tab.page_source

    async def evaluate(self, script: str) -> Any:
        tab = await self._get_tab()
        result = await tab.execute_script(
            script,
            return_by_value=True,
            await_promise=True,
        )
        return self._unwrap_script_result(result)

    async def cookies(self) -> dict[str, str]:
        tab = await self._get_tab()
        cookies = await tab.get_cookies()
        return {str(cookie["name"]): str(cookie["value"]) for cookie in cookies}

    async def set_cookies(self, cookies: dict[str, str]) -> None:
        tab = await self._get_tab()
        await tab.set_cookies(
            [{"name": name, "value": value} for name, value in cookies.items()]
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> BrowserResponse:
        tab = await self._get_tab()
        pydoll_headers = self._headers_to_entries(headers)
        response = await tab.request.request(
            method.upper(),
            url,
            headers=pydoll_headers or None,
            data=data,
        )
        return BrowserResponse(
            status_code=response.status_code,
            headers=self._entries_to_headers(response.headers),
            body=response.text,
        )

    async def download(self, url: str, path: Path) -> Path:
        tab = await self._get_tab()
        response = await tab.request.get(url)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return path

    async def screenshot(self, path: Path) -> Path:
        tab = await self._get_tab()
        path.parent.mkdir(parents=True, exist_ok=True)
        await tab.take_screenshot(path)
        return path

    async def close(self) -> None:
        if self._tab is not None:
            for callback_id in self._network_callback_ids:
                try:
                    await self._tab.remove_callback(callback_id)
                except Exception:
                    logger.debug("Could not remove Pydoll callback", exc_info=True)
            self._network_callback_ids.clear()
        if self._browser is not None and self._owns_browser:
            try:
                await self._browser.stop()
            except Exception:
                logger.debug("Could not stop Pydoll browser", exc_info=True)
        self._tab = None
        self._browser = None
        self._network_tracking_enabled = False
        self._inflight_requests.clear()

    async def _get_tab(self) -> Any:
        if self._tab is None:
            async with self._start_lock:
                if self._tab is None:
                    await self._start()
        await self._enable_network_tracking()
        return self._tab

    async def _start(self) -> None:
        if self._browser is None:
            try:
                from pydoll.browser.chromium import Chrome
                from pydoll.browser.options import ChromiumOptions
            except ImportError as error:
                raise PydollNotInstalledError(
                    "Pydoll is not installed; install Crawlerflow with the 'browser' extra"
                ) from error

            options = ChromiumOptions()
            options.headless = self.config.headless
            options.start_timeout = self.config.start_timeout
            if self.config.binary_location is not None:
                options.binary_location = str(self.config.binary_location)
            if self.config.download_directory is not None:
                self.config.download_directory.mkdir(parents=True, exist_ok=True)
                options.set_default_download_directory(str(self.config.download_directory))
            for argument in self.config.arguments:
                options.add_argument(argument)

            self._browser = Chrome(options=options)
        self._tab = await self._browser.start()

    async def _query(self, selector: str, timeout_seconds: float | None = None) -> Any:
        tab = await self._get_tab()
        timeout = timeout_seconds or self.config.default_wait_timeout
        return await tab.query(selector, timeout=max(1, math.ceil(timeout)), raise_exc=True)

    async def _enable_network_tracking(self) -> None:
        if self._network_tracking_enabled:
            return
        try:
            from pydoll.protocol.network.events import NetworkEvent
        except ImportError as error:
            raise PydollNotInstalledError(
                "Pydoll is not installed; install Crawlerflow with the 'browser' extra"
            ) from error

        await self._tab.enable_network_events()
        self._network_callback_ids = [
            await self._tab.on(NetworkEvent.REQUEST_WILL_BE_SENT, self._on_request_started),
            await self._tab.on(NetworkEvent.LOADING_FINISHED, self._on_request_finished),
            await self._tab.on(NetworkEvent.LOADING_FAILED, self._on_request_finished),
        ]
        self._network_tracking_enabled = True
        self._last_network_activity = time.monotonic()

    async def _on_request_started(self, event: Mapping[str, Any]) -> None:
        request_id = self._request_id(event)
        if request_id is not None:
            self._inflight_requests.add(request_id)
        self._last_network_activity = time.monotonic()

    async def _on_request_finished(self, event: Mapping[str, Any]) -> None:
        request_id = self._request_id(event)
        if request_id is not None:
            self._inflight_requests.discard(request_id)
        self._last_network_activity = time.monotonic()

    @staticmethod
    def _request_id(event: Mapping[str, Any]) -> str | None:
        parameters = event.get("params", event)
        if not isinstance(parameters, Mapping):
            return None
        request_id = parameters.get("requestId")
        return str(request_id) if request_id is not None else None

    @staticmethod
    def _unwrap_script_result(response: Mapping[str, Any]) -> Any:
        payload = response.get("result", response)
        if not isinstance(payload, Mapping):
            return payload
        exception = payload.get("exceptionDetails")
        if isinstance(exception, Mapping):
            message = exception.get("text", "JavaScript execution failed")
            remote_exception = exception.get("exception")
            if isinstance(remote_exception, Mapping):
                message = remote_exception.get("description", message)
            raise PydollAdapterError(str(message))

        remote_object = payload.get("result", payload)
        if not isinstance(remote_object, Mapping):
            return remote_object
        if "value" in remote_object:
            return remote_object["value"]
        if remote_object.get("type") == "undefined":
            return None
        return remote_object.get("unserializableValue", remote_object.get("description"))

    @staticmethod
    def _headers_to_entries(headers: dict[str, str] | None) -> list[dict[str, str]]:
        if headers is None:
            return []
        return [{"name": name, "value": value} for name, value in headers.items()]

    @staticmethod
    def _entries_to_headers(entries: Any) -> dict[str, str]:
        if isinstance(entries, Mapping):
            return {str(name): str(value) for name, value in entries.items()}
        return {
            str(entry["name"]): str(entry["value"])
            for entry in entries or []
            if "name" in entry and "value" in entry
        }

    @staticmethod
    def _json_string(value: str) -> str:
        return json.dumps(value)
