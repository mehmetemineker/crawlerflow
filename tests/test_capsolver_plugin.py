from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest


def load_capsolver_plugin(monkeypatch: pytest.MonkeyPatch):
    package_root = Path("examples/plugins/capsolver")
    monkeypatch.syspath_prepend(str(package_root / "src"))
    return importlib.import_module("crawlerflow_capsolver")


@pytest.mark.asyncio
async def test_capsolver_client_supports_generic_sync_and_async_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    poll_count = 0
    create_count = 0

    def async_handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count, poll_count
        requests.append(request)
        if request.url.path == "/createTask":
            create_count += 1
            if create_count == 1:
                return httpx.Response(
                    200,
                    json={"errorId": 0, "status": "ready", "solution": {"text": "1234"}},
                )
            return httpx.Response(200, json={"errorId": 0, "taskId": "task-1", "status": "idle"})
        if request.url.path == "/getTaskResult":
            poll_count += 1
            return httpx.Response(
                200,
                json={
                    "errorId": 0,
                    "status": "ready",
                    "taskId": "task-1",
                    "solution": {"gRecaptchaResponse": "token"},
                },
            )
        if request.url.path == "/getToken":
            return httpx.Response(
                200,
                json={"errorId": 0, "status": "ready", "solution": {"token": "abc"}},
            )
        return httpx.Response(200, json={"errorId": 0, "balance": 12.5})

    transport = httpx.MockTransport(async_handler)
    module = load_capsolver_plugin(monkeypatch)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = module.CapSolverClient(
            "secret-key",
            base_url="https://capsolver.test",
            poll_interval=0,
            http_client=http_client,
        )
        sync = await client.solve({"type": "ImageToTextTask", "body": "base64"})
        async_result = await client.solve({"type": "ReCaptchaV3TaskProxyLess"})
        token = await client.get_token({"type": "ReCaptchaV2TaskProxyLess"})
        balance = await client.get_balance()

    assert sync["solution"]["text"] == "1234"
    assert async_result["solution"]["gRecaptchaResponse"] == "token"
    assert token["solution"]["token"] == "abc"
    assert balance["balance"] == 12.5
    assert poll_count == 1
    assert all(request.content and b"secret-key" in request.content for request in requests)


def test_capsolver_plugin_registers_api_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_capsolver_plugin(monkeypatch)

    from crawlerflow.engine.registry import StepRegistry
    from crawlerflow.events import EventBus
    from crawlerflow.expressions import ExpressionEngine
    from crawlerflow.plugins import PluginRegistrationContext

    registry = StepRegistry()
    module.CapSolverPlugin().register(
        PluginRegistrationContext(registry, ExpressionEngine(), EventBus())
    )

    assert set(registry.names()) == {
        "capsolver_create_task",
        "capsolver_get_balance",
        "capsolver_get_state",
        "capsolver_get_task_result",
        "capsolver_get_token",
        "capsolver_request",
        "capsolver_solve",
    }
