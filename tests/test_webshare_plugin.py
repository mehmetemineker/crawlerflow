from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest


def load_webshare_plugin(monkeypatch: pytest.MonkeyPatch):
    package_root = Path("examples/plugins/webshare")
    monkeypatch.syspath_prepend(str(package_root / "src"))
    return importlib.import_module("crawlerflow_webshare")


@pytest.mark.asyncio
async def test_webshare_selects_random_valid_proxy_and_reads_all_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_webshare_plugin(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json={
                    "count": 3,
                    "next": "https://proxy.test/api/v2/proxy/list/?page=2",
                    "results": [
                        {
                            "id": "bad",
                            "proxy_address": "192.0.2.1",
                            "port": 8000,
                            "username": "user",
                            "password": "pass",
                            "valid": False,
                        },
                        {
                            "id": "good-1",
                            "proxy_address": "192.0.2.2",
                            "port": 8001,
                            "username": "user",
                            "password": "pass",
                            "valid": True,
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 3,
                "next": None,
                "results": [
                    {
                        "id": "good-2",
                        "proxy_address": "192.0.2.3",
                        "port": 8002,
                        "username": "user",
                        "password": "pass",
                        "valid": True,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = module.WebshareClient(
            "api-key",
            base_url="https://proxy.test",
            http_client=http_client,
        )
        proxies = await client.list_proxies()

    assert [proxy.proxy_id for proxy in proxies] == ["good-1", "good-2"]
    assert all(request.headers["Authorization"] == "Token api-key" for request in requests)
    assert proxies[0].url == "http://user:pass@192.0.2.2:8001"


def test_webshare_plugin_registers_proxy_info_step(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_webshare_plugin(monkeypatch)

    from crawlerflow.engine.registry import StepRegistry
    from crawlerflow.events import EventBus
    from crawlerflow.expressions import ExpressionEngine
    from crawlerflow.plugins import PluginRegistrationContext

    registry = StepRegistry()
    module.WebsharePlugin().register(
        PluginRegistrationContext(registry, ExpressionEngine(), EventBus())
    )

    assert tuple(registry.names()) == ("webshare_proxy_info",)


def test_webshare_example_workflow_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_webshare_plugin(monkeypatch)

    from crawlerflow.engine.runner import WorkflowRunner

    document = WorkflowRunner(plugins=[module.WebsharePlugin()]).load(
        "examples/plugins/webshare/workflow.yaml"
    )

    assert document.workflow.name == "webshare-proxy-example"
