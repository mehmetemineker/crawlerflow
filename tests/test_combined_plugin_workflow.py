from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def test_capsolver_webshare_example_workflow_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path("examples/plugins/capsolver") / "src"))
    monkeypatch.syspath_prepend(str(Path("examples/plugins/webshare") / "src"))
    capsolver = importlib.import_module("crawlerflow_capsolver")
    webshare = importlib.import_module("crawlerflow_webshare")

    from crawlerflow.engine.runner import WorkflowRunner

    document = WorkflowRunner(
        plugins=[webshare.WebsharePlugin(), capsolver.CapSolverPlugin()]
    ).load("examples/plugins/capsolver-webshare/workflow.yaml")

    assert document.workflow.name == "capsolver-webshare-example"
    assert [step.name for step in document.steps] == [
        "webshare_proxy_info",
        "capsolver_solve",
        "http_request",
        "save_json",
    ]

    cloudflare_document = WorkflowRunner(
        plugins=[webshare.WebsharePlugin(), capsolver.CapSolverPlugin()]
    ).load("examples/plugins/capsolver-webshare/cloudflare-challenge.yaml")

    assert cloudflare_document.workflow.name == "cloudflare-challenge-example"
    assert [step.name for step in cloudflare_document.steps] == [
        "webshare_proxy_info",
        "capsolver_solve",
        "goto",
        "set_cookies",
        "goto",
        "evaluate",
        "save_json",
    ]

    http_document = WorkflowRunner(
        plugins=[webshare.WebsharePlugin(), capsolver.CapSolverPlugin()]
    ).load("examples/plugins/capsolver-webshare/cloudflare-challenge-http.yaml")

    assert http_document.workflow.name == "cloudflare-challenge-http-example"
    assert [step.name for step in http_document.steps] == [
        "webshare_proxy_info",
        "http_request",
        "capsolver_solve",
        "http_request",
        "save_html",
    ]
