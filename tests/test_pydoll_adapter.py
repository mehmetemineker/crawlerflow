from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crawlerflow.browser.factory import create_browser_adapter
from crawlerflow.browser.pydoll import (
    NetworkIdleTimeout,
    PydollAdapterError,
    PydollBrowserAdapter,
    PydollBrowserConfig,
)
from crawlerflow.workflow.models import BrowserSettings


class FakeElement:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.selected_value = ""

    async def click(self) -> None:
        self.calls.append("click")

    async def clear(self) -> None:
        self.calls.append("clear")

    async def insert_text(self, value: str) -> None:
        self.calls.append(("insert_text", value))

    async def execute_script(self, script: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("execute_script", script, kwargs))
        marker = "this.value = "
        value_start = script.index(marker) + len(marker)
        self.selected_value = script[value_start:].split(";", 1)[0].strip().strip('"')
        return {"result": {"result": {"type": "string", "value": self.selected_value}}}


class FakeResponse:
    status_code = 200
    text = "response body"
    content = b"download body"
    headers = [{"name": "content-type", "value": "text/plain"}]

    def raise_for_status(self) -> None:
        return None


class FakeRequest:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return FakeResponse()

    async def get(self, url: str) -> FakeResponse:
        self.calls.append(("GET", url, {}))
        return FakeResponse()


class FakeTab:
    def __init__(self) -> None:
        self.element = FakeElement()
        self.request = FakeRequest()
        self.calls: list[Any] = []
        self.callbacks: dict[int, Any] = {}
        self.callback_sequence = 0

    async def enable_network_events(self) -> None:
        self.calls.append("enable_network_events")

    async def on(self, event_name: Any, callback: Any) -> int:
        self.callback_sequence += 1
        self.callbacks[self.callback_sequence] = (event_name, callback)
        return self.callback_sequence

    async def remove_callback(self, callback_id: int) -> None:
        self.callbacks.pop(callback_id)

    async def go_to(self, url: str) -> None:
        self.calls.append(("go_to", url))

    async def query(self, selector: str, **kwargs: Any) -> FakeElement:
        self.calls.append(("query", selector, kwargs))
        return self.element

    @property
    async def page_source(self) -> str:
        return "<html>Example</html>"

    async def execute_script(self, script: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("execute_script", script, kwargs))
        return {"result": {"result": {"type": "string", "value": "Example"}}}

    async def get_cookies(self) -> list[dict[str, str]]:
        return [{"name": "session", "value": "abc"}]

    async def set_cookies(self, cookies: list[dict[str, str]]) -> None:
        self.calls.append(("set_cookies", cookies))

    async def take_screenshot(self, path: Path) -> None:
        path.write_bytes(b"image")


class FailingBrowser:
    async def stop(self) -> None:
        raise RuntimeError("browser is already stopped")


@pytest.mark.asyncio
async def test_maps_browser_operations_to_pydoll(tmp_path: Path) -> None:
    tab = FakeTab()
    adapter = PydollBrowserAdapter(
        PydollBrowserConfig(default_wait_timeout=2),
        tab=tab,
    )

    await adapter.goto("https://example.com")
    await adapter.click("#submit")
    await adapter.fill("#name", "Crawlerflow")
    await adapter.select("#city", "34")
    evaluated = await adapter.evaluate("return document.title")
    html = await adapter.html()
    cookies = await adapter.cookies()
    await adapter.set_cookies({"token": "secret"})
    response = await adapter.request(
        "post",
        "/api",
        headers={"X-Test": "yes"},
        data={"ok": True},
    )
    screenshot_path = await adapter.screenshot(tmp_path / "shots" / "page.png")
    download_path = await adapter.download("/file", tmp_path / "downloads" / "file.bin")
    await adapter.close()

    assert ("go_to", "https://example.com") in tab.calls
    assert tab.element.calls[:3] == ["click", "clear", ("insert_text", "Crawlerflow")]
    assert tab.element.selected_value == "34"
    assert evaluated == "Example"
    assert html == "<html>Example</html>"
    assert cookies == {"session": "abc"}
    assert response.status_code == 200
    assert response.headers == {"content-type": "text/plain"}
    assert tab.request.calls[0][2]["headers"] == [{"name": "X-Test", "value": "yes"}]
    assert screenshot_path.read_bytes() == b"image"
    assert download_path.read_bytes() == b"download body"


@pytest.mark.asyncio
async def test_wait_network_tracks_inflight_requests() -> None:
    adapter = PydollBrowserAdapter(
        PydollBrowserConfig(default_wait_timeout=1, network_idle_period=0.01),
        tab=FakeTab(),
    )
    await adapter._on_request_started({"params": {"requestId": "request-1"}})
    waiter = asyncio.create_task(adapter.wait_network())
    await asyncio.sleep(0.02)
    assert waiter.done() is False

    await adapter._on_request_finished({"params": {"requestId": "request-1"}})

    await waiter


@pytest.mark.asyncio
async def test_wait_network_times_out() -> None:
    adapter = PydollBrowserAdapter(
        PydollBrowserConfig(default_wait_timeout=0.02, network_idle_period=0.01),
        tab=FakeTab(),
    )
    await adapter._on_request_started({"requestId": "request-1"})

    with pytest.raises(NetworkIdleTimeout, match="still active"):
        await adapter.wait_network()


def test_unwraps_javascript_errors() -> None:
    response = {
        "result": {
            "exceptionDetails": {
                "text": "Uncaught",
                "exception": {"description": "Error: broken script"},
            }
        }
    }

    with pytest.raises(PydollAdapterError, match="broken script"):
        PydollBrowserAdapter._unwrap_script_result(response)


def test_factory_resolves_workflow_browser_settings(tmp_path: Path) -> None:
    disabled = BrowserSettings()
    enabled = BrowserSettings.model_validate(
        {
            "headless": False,
            "arguments": ["--window-size=1280,720"],
            "binary_location": "bin/chrome.exe",
            "download_directory": "downloads",
        }
    )

    assert create_browser_adapter(disabled, base_path=tmp_path) is None
    adapter = create_browser_adapter(enabled, base_path=tmp_path)

    assert isinstance(adapter, PydollBrowserAdapter)
    assert adapter.config.headless is False
    assert adapter.config.arguments == ("--window-size=1280,720",)
    assert adapter.config.binary_location == tmp_path / "bin" / "chrome.exe"
    assert adapter.config.download_directory == tmp_path / "downloads"


@pytest.mark.asyncio
async def test_close_is_idempotent_when_browser_already_stopped() -> None:
    adapter = PydollBrowserAdapter(browser=FailingBrowser())
    adapter._owns_browser = True

    await adapter.close()
    await adapter.close()
