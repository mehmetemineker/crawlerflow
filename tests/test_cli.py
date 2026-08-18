from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from crawlerflow.cli import app as cli_module
from crawlerflow.plugins import PluginInfo


def test_run_accepts_multiple_workflow_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_paths = [tmp_path / "first.yaml", tmp_path / "second.yaml"]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")
    calls: list[Path] = []

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            calls.append(workflow)
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run", *(str(workflow_path) for workflow_path in workflow_paths)],
    )

    assert result.exit_code == 0
    assert calls == workflow_paths
    assert "Completed: first" in result.stdout
    assert "Completed: second" in result.stdout


def test_run_discovers_yaml_files_in_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_directory = tmp_path / "workflows"
    workflow_directory.mkdir()
    expected_paths = [workflow_directory / "a.yaml", workflow_directory / "b.yml"]
    for workflow_path in reversed(expected_paths):
        workflow_path.write_text("version: 1", encoding="utf-8")
    (workflow_directory / "ignored.txt").write_text("ignored", encoding="utf-8")
    nested_directory = workflow_directory / "nested"
    nested_directory.mkdir()
    (nested_directory / "nested.yaml").write_text("version: 1", encoding="utf-8")
    calls: list[Path] = []

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            calls.append(workflow)
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(cli_module.app, ["run", str(workflow_directory)])

    assert result.exit_code == 0
    assert calls == expected_paths
    assert "ignored" not in result.stdout
    assert "nested" not in result.stdout


def test_run_rejects_directory_without_yaml_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_directory = tmp_path / "empty"
    empty_directory.mkdir()

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            pytest.fail(f"Unexpected workflow execution: {workflow}")

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(cli_module.app, ["run", str(empty_directory)])

    assert result.exit_code == 1
    assert "No workflow YAML files found" in result.stdout


def test_run_deduplicates_explicit_and_discovered_workflow_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text("version: 1", encoding="utf-8")
    calls: list[Path] = []

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            calls.append(workflow)
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run", str(workflow_path), str(tmp_path)],
    )

    assert result.exit_code == 0
    assert calls == [workflow_path]


def test_run_stops_after_first_failed_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_paths = [tmp_path / f"{name}.yaml" for name in ("first", "failed", "third")]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")
    calls: list[Path] = []

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            calls.append(workflow)
            if workflow.stem == "failed":
                raise ValueError("invalid test workflow")
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run", *(str(workflow_path) for workflow_path in workflow_paths)],
    )

    assert result.exit_code == 1
    assert calls == workflow_paths[:2]
    assert "Completed: first" in result.stdout
    assert "Workflow failed:" in result.stdout
    assert workflow_paths[1].name in result.stdout
    assert "third" not in result.stdout


def test_run_async_mode_executes_workflows_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_paths = [tmp_path / "first.yaml", tmp_path / "second.yaml"]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")
    active = 0
    peak_active = 0

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run",
            "--mode",
            "async",
            *(str(workflow_path) for workflow_path in workflow_paths),
        ],
    )

    assert result.exit_code == 0
    assert peak_active == 2
    assert "Completed: first" in result.stdout
    assert "Completed: second" in result.stdout


def test_run_async_mode_limits_concurrent_workflows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_paths = [tmp_path / f"workflow-{index}.yaml" for index in range(5)]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")
    active = 0
    peak_active = 0

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            try:
                await asyncio.sleep(0.01)
            finally:
                active -= 1
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run",
            "--mode",
            "async",
            "--concurrency",
            "2",
            *(str(workflow_path) for workflow_path in workflow_paths),
        ],
    )

    assert result.exit_code == 0
    assert peak_active == 2
    for workflow_path in workflow_paths:
        assert f"Completed: {workflow_path.stem}" in result.stdout


def test_run_rejects_concurrency_in_sync_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text("version: 1", encoding="utf-8")

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            pytest.fail(f"Unexpected workflow execution: {workflow}")

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run", "--concurrency", "2", str(workflow_path)],
    )

    assert result.exit_code == 1
    assert "--concurrency requires --mode async" in result.stdout


def test_run_async_mode_logs_each_workflow_when_it_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_paths = [tmp_path / "slow.yaml", tmp_path / "fast.yaml"]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            await asyncio.sleep(0.03 if workflow.stem == "slow" else 0)
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run",
            "--mode",
            "async",
            *(str(workflow_path) for workflow_path in workflow_paths),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.index("Completed: fast") < result.stdout.index("Completed: slow")


def test_run_async_mode_waits_for_all_workflows_when_one_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_paths = [tmp_path / f"{name}.yaml" for name in ("first", "failed", "third")]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")
    completed: list[str] = []

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            await asyncio.sleep(0)
            if workflow.stem == "failed":
                raise ValueError("invalid test workflow")
            completed.append(workflow.stem)
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run",
            "--mode",
            "async",
            *(str(workflow_path) for workflow_path in workflow_paths),
        ],
    )

    assert result.exit_code == 1
    assert completed == ["first", "third"]
    assert "Completed: first" in result.stdout
    assert "Workflow failed:" in result.stdout
    assert "failed.yaml" in result.stdout
    assert "Completed: third" in result.stdout


@pytest.mark.parametrize("mode_options", [[], ["--mode", "async"]])
def test_run_optionally_shows_live_workflow_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode_options: list[str],
) -> None:
    workflow_paths = [tmp_path / "first.yaml", tmp_path / "second.yaml"]
    for workflow_path in workflow_paths:
        workflow_path.write_text("version: 1", encoding="utf-8")

    class FakeRunner:
        async def run(self, workflow: Path) -> SimpleNamespace:
            await asyncio.sleep(0)
            return SimpleNamespace(workflow_name=workflow.stem)

    monkeypatch.setattr(cli_module, "WorkflowRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run",
            *mode_options,
            "--progress",
            *(str(workflow_path) for workflow_path in workflow_paths),
        ],
    )

    assert result.exit_code == 0
    assert "Workflows" in result.stdout
    assert "2/2" in result.stdout


def test_list_plugins_displays_installed_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "discover_plugins",
        lambda: (
            PluginInfo(
                name="example",
                target="crawlerflow_example_plugin:ExamplePlugin",
                distribution="crawlerflow-example-plugin",
            ),
        ),
    )

    result = CliRunner().invoke(cli_module.app, ["list-plugins"])

    assert result.exit_code == 0
    assert "example" in result.stdout
    assert "crawlerflow_example_plugin:ExamplePlugin" in result.stdout
    assert "crawlerflow-example-plugin" in result.stdout


def test_list_plugins_reports_empty_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "discover_plugins", lambda: ())

    result = CliRunner().invoke(cli_module.app, ["list-plugins"])

    assert result.exit_code == 0
    assert "No plugins installed" in result.stdout
