from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import pytest


def load_plugin_modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path("examples/plugins/capsolver") / "src"))
    monkeypatch.syspath_prepend(str(Path("examples/plugins/webshare") / "src"))
    return (
        importlib.import_module("crawlerflow_capsolver"),
        importlib.import_module("crawlerflow_webshare"),
    )


@pytest.mark.asyncio
async def test_capsolver_retries_same_proxy_then_switches_to_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsolver, webshare = load_plugin_modules(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(
                200,
                json={
                    "errorId": 1,
                    "errorCode": "ERROR_PROXY_CONNECT",
                    "errorDescription": "custom proxy connect",
                },
            )
        return httpx.Response(
            200,
            json={"errorId": 0, "status": "ready", "solution": {"token": "ok"}},
        )

    from crawlerflow.engine.context import WorkflowContext

    context = WorkflowContext(workflow_name="retry", base_path=Path.cwd())
    retry = webshare.WebshareProxyRetrySettings(
        enabled=True,
        attempts_per_proxy=2,
        max_proxies=2,
    )
    session = webshare.WebshareProxySession(
        None,
        [
            webshare.WebshareProxy("proxy-1", "192.0.2.1", 8001, "u", "p"),
            webshare.WebshareProxy("proxy-2", "192.0.2.2", 8002, "u", "p"),
        ],
        retry=retry,
    )
    await session.select_initial(context)
    initial_proxy_url = session.current_proxy.url
    context.storage["webshare_proxy_session"] = session
    context.plugin_settings["capsolver"] = capsolver.CapSolverSettings(poll_interval=0)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        context.storage["capsolver_client"] = capsolver.CapSolverClient(
            "secret-key",
            base_url="https://capsolver.test",
            http_client=http_client,
        )
        step = capsolver.CapSolverSolveStep(
            capsolver._TaskStepConfig(
                task={"type": "AntiCloudflareTask", "proxy": "initial"},
                save_as="result",
            )
        )
        result = await step.execute(context)

    assert result["solution"]["token"] == "ok"
    assert len(requests) == 3
    sent_tasks = [json.loads(request.content)["task"] for request in requests]
    assert sent_tasks[0]["proxy"] == initial_proxy_url
    assert sent_tasks[1]["proxy"] == initial_proxy_url
    assert sent_tasks[2]["proxy"] != initial_proxy_url
    assert context.proxy_url == sent_tasks[2]["proxy"]


@pytest.mark.asyncio
async def test_capsolver_does_not_rotate_for_non_proxy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsolver, webshare = load_plugin_modules(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "errorId": 1,
                "errorCode": "ERROR_ZERO_BALANCE",
                "errorDescription": "balance is insufficient",
            },
        )

    from crawlerflow.engine.context import WorkflowContext

    context = WorkflowContext(workflow_name="no-rotate", base_path=Path.cwd())
    session = webshare.WebshareProxySession(
        None,
        [webshare.WebshareProxy("proxy-1", "192.0.2.1", 8001)],
        retry=webshare.WebshareProxyRetrySettings(
            enabled=True,
            attempts_per_proxy=3,
            max_proxies=2,
        ),
    )
    await session.select_initial(context)
    context.storage["webshare_proxy_session"] = session
    context.plugin_settings["capsolver"] = capsolver.CapSolverSettings(poll_interval=0)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        context.storage["capsolver_client"] = capsolver.CapSolverClient(
            "secret-key",
            base_url="https://capsolver.test",
            http_client=http_client,
        )
        step = capsolver.CapSolverSolveStep(
            capsolver._TaskStepConfig(
                task={"type": "AntiCloudflareTask", "proxy": "initial"},
                save_as="result",
            )
        )
        with pytest.raises(capsolver.CapSolverError, match="balance"):
            await step.execute(context)

    assert len(requests) == 1
    assert session.current_proxy.proxy_id == "proxy-1"
