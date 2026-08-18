from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

from crawlerflow.engine.runner import WorkflowRunner


@pytest.mark.asyncio
async def test_external_example_plugin_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = Path("examples/plugins/example")
    pyproject = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["crawlerflow.plugins"]
    assert entry_points["example"] == "crawlerflow_example_plugin:ExamplePlugin"

    monkeypatch.syspath_prepend(str(package_root / "src"))
    plugin_module = importlib.import_module("crawlerflow_example_plugin")
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        (package_root / "workflow.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    await WorkflowRunner(plugins=[plugin_module.ExamplePlugin()]).run(workflow_path)

    result_path = tmp_path / "output" / "plugin-result.txt"
    assert result_path.read_text(encoding="utf-8") == "<<CrawlerFlow plugin works>>"
