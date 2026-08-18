from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.executor import WorkflowExecutionError
from crawlerflow.engine.registry import BaseStep
from crawlerflow.engine.runner import WorkflowRunner
from crawlerflow.plugins import (
    DuplicatePluginError,
    PluginConfigurationError,
    PluginInfo,
    PluginLifecycleError,
    PluginNotFoundError,
    PluginRegistrationContext,
    discover_plugins,
)
from crawlerflow.plugins import manager as plugin_manager_module


class SetOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: Any


class SetOutputStep(BaseStep[SetOutputConfig]):
    config_model = SetOutputConfig

    async def execute(self, context: WorkflowContext) -> Any:
        context.outputs[self.config.key] = self.config.value
        return self.config.value


class ExamplePlugin:
    name = "example"

    def register(self, context: PluginRegistrationContext) -> None:
        context.registry.register("set_output", SetOutputStep)
        context.expression_engine.register_filter("reverse", lambda value: str(value)[::-1])


class RecordingPlugin:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def register(self, context: PluginRegistrationContext) -> None:
        pass

    def startup(self, context: WorkflowContext) -> None:
        self.calls.append(f"{self.name}.startup")
        context.outputs["lifecycle"] = "ready"

    def shutdown(self, context: WorkflowContext) -> None:
        self.calls.append(f"{self.name}.shutdown")


class AsyncRecordingPlugin(RecordingPlugin):
    async def startup(self, context: WorkflowContext) -> None:
        super().startup(context)

    async def shutdown(self, context: WorkflowContext) -> None:
        super().shutdown(context)


class FailingStartupPlugin(RecordingPlugin):
    async def startup(self, context: WorkflowContext) -> None:
        super().startup(context)
        raise RuntimeError("cannot start")


class FailingShutdownPlugin(RecordingPlugin):
    async def shutdown(self, context: WorkflowContext) -> None:
        super().shutdown(context)
        raise RuntimeError("cannot stop")


class ConfiguredSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(min_length=1)


class ConfiguredPlugin:
    name = "configured"
    settings_model = ConfiguredSettings

    def register(self, context: PluginRegistrationContext) -> None:
        pass

    def startup(self, context: WorkflowContext) -> None:
        settings = context.plugin_settings[self.name]
        if not isinstance(settings, ConfiguredSettings):
            raise TypeError("Expected validated ConfiguredSettings")
        context.outputs["configured_message"] = f"{settings.prefix} crawler"


class FakeEntryPoint:
    name = "example"
    value = "example_package:ExamplePlugin"
    dist = None

    @staticmethod
    def load() -> type[ExamplePlugin]:
        return ExamplePlugin


def write_plugin_workflow(path: Path, *, configured: bool = False) -> None:
    plugins = "plugins:\n  - example\n" if configured else ""
    path.write_text(
        f"""
version: 1
workflow:
  name: plugin-test
{plugins}steps:
  - set_output:
      key: message
      value: crawler
  - save_text:
      path: result.txt
      content: "{{{{message|reverse}}}}"
""".strip(),
        encoding="utf-8",
    )


def write_lifecycle_workflow(path: Path, *, failing: bool = False) -> None:
    if failing:
        step = "  - goto:\n      url: https://example.com"
    else:
        step = '  - save_text:\n      path: lifecycle.txt\n      content: "{{lifecycle}}"'
    path.write_text(
        f"""
version: 1
workflow:
  name: lifecycle-test
steps:
{step}
""".strip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_explicit_plugin_adds_steps_and_filters(tmp_path: Path) -> None:
    workflow_path = tmp_path / "plugin.yaml"
    write_plugin_workflow(workflow_path)

    await WorkflowRunner(plugins=[ExamplePlugin()]).run(workflow_path)

    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "relwarc"


def test_plugin_registrations_are_isolated_between_runners(tmp_path: Path) -> None:
    workflow_path = tmp_path / "plugin.yaml"
    write_plugin_workflow(workflow_path)

    WorkflowRunner(plugins=[ExamplePlugin()]).load(workflow_path)

    with pytest.raises(ValueError, match="Unknown step: set_output"):
        WorkflowRunner().load(workflow_path)


def test_duplicate_explicit_plugin_is_rejected() -> None:
    with pytest.raises(DuplicatePluginError, match="already registered"):
        WorkflowRunner(plugins=[ExamplePlugin(), ExamplePlugin()])


@pytest.mark.asyncio
async def test_configured_plugin_loads_from_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "plugin.yaml"
    write_plugin_workflow(workflow_path, configured=True)
    monkeypatch.setattr(
        plugin_manager_module.metadata,
        "entry_points",
        lambda **kwargs: [FakeEntryPoint()],
    )

    runner = WorkflowRunner()
    await runner.run(workflow_path)

    assert runner.plugin_manager.loaded_names == ("example",)
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "relwarc"


def test_missing_configured_plugin_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "plugin.yaml"
    write_plugin_workflow(workflow_path, configured=True)
    monkeypatch.setattr(plugin_manager_module.metadata, "entry_points", lambda **kwargs: [])

    with pytest.raises(PluginNotFoundError, match="not installed"):
        WorkflowRunner().load(workflow_path)


def test_discovers_plugin_metadata_without_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_manager_module.metadata,
        "entry_points",
        lambda **kwargs: [FakeEntryPoint()],
    )

    assert discover_plugins() == (
        PluginInfo(name="example", target="example_package:ExamplePlugin"),
    )


def test_duplicate_installed_plugin_name_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "plugin.yaml"
    write_plugin_workflow(workflow_path, configured=True)
    monkeypatch.setattr(
        plugin_manager_module.metadata,
        "entry_points",
        lambda **kwargs: [FakeEntryPoint(), FakeEntryPoint()],
    )

    with pytest.raises(DuplicatePluginError, match="Multiple installed plugins"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_plugin_lifecycle_uses_registration_and_reverse_shutdown_order(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "lifecycle.yaml"
    write_lifecycle_workflow(workflow_path)
    calls: list[str] = []
    plugins = [
        RecordingPlugin("first", calls),
        AsyncRecordingPlugin("second", calls),
    ]

    await WorkflowRunner(plugins=plugins).run(workflow_path)

    assert calls == [
        "first.startup",
        "second.startup",
        "second.shutdown",
        "first.shutdown",
    ]
    assert (tmp_path / "lifecycle.txt").read_text(encoding="utf-8") == "ready"


@pytest.mark.asyncio
async def test_started_plugins_shutdown_when_later_startup_fails(tmp_path: Path) -> None:
    workflow_path = tmp_path / "lifecycle.yaml"
    write_lifecycle_workflow(workflow_path)
    calls: list[str] = []
    plugins = [
        RecordingPlugin("first", calls),
        FailingStartupPlugin("second", calls),
        RecordingPlugin("third", calls),
    ]

    with pytest.raises(PluginLifecycleError, match=r"second\.startup: cannot start"):
        await WorkflowRunner(plugins=plugins).run(workflow_path)

    assert calls == ["first.startup", "second.startup", "first.shutdown"]


@pytest.mark.asyncio
async def test_plugin_shutdown_runs_after_workflow_failure(tmp_path: Path) -> None:
    workflow_path = tmp_path / "lifecycle.yaml"
    write_lifecycle_workflow(workflow_path, failing=True)
    calls: list[str] = []

    with pytest.raises(WorkflowExecutionError):
        await WorkflowRunner(plugins=[RecordingPlugin("plugin", calls)]).run(workflow_path)

    assert calls == ["plugin.startup", "plugin.shutdown"]


@pytest.mark.asyncio
async def test_all_plugin_shutdown_hooks_run_when_one_fails(tmp_path: Path) -> None:
    workflow_path = tmp_path / "lifecycle.yaml"
    write_lifecycle_workflow(workflow_path)
    calls: list[str] = []
    plugins = [
        FailingShutdownPlugin("first", calls),
        RecordingPlugin("second", calls),
    ]

    with pytest.raises(PluginLifecycleError, match=r"first\.shutdown: cannot stop"):
        await WorkflowRunner(plugins=plugins).run(workflow_path)

    assert calls[-2:] == ["second.shutdown", "first.shutdown"]


@pytest.mark.asyncio
async def test_shutdown_failure_does_not_replace_workflow_failure(tmp_path: Path) -> None:
    workflow_path = tmp_path / "lifecycle.yaml"
    write_lifecycle_workflow(workflow_path, failing=True)
    calls: list[str] = []

    with pytest.raises(WorkflowExecutionError) as captured:
        await WorkflowRunner(
            plugins=[FailingShutdownPlugin("plugin", calls)]
        ).run(workflow_path)

    assert any("plugin.shutdown: cannot stop" in note for note in captured.value.__notes__)


@pytest.mark.asyncio
async def test_plugin_settings_are_validated_and_available_at_startup(tmp_path: Path) -> None:
    workflow_path = tmp_path / "configured.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: configured-plugin
plugins:
  - name: configured
    settings:
      prefix: hello
steps:
  - save_text:
      path: configured.txt
      content: "{{configured_message}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner(plugins=[ConfiguredPlugin()]).run(workflow_path)

    assert (tmp_path / "configured.txt").read_text(encoding="utf-8") == "hello crawler"


def test_invalid_plugin_settings_fail_during_validation(tmp_path: Path) -> None:
    workflow_path = tmp_path / "configured.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-plugin-settings
plugins:
  - name: configured
    settings:
      prefix: ""
steps:
  - log:
      message: unreachable
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PluginConfigurationError, match="Invalid settings"):
        WorkflowRunner(plugins=[ConfiguredPlugin()]).load(workflow_path)


def test_plugin_without_settings_model_rejects_settings(tmp_path: Path) -> None:
    workflow_path = tmp_path / "configured.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: unsupported-plugin-settings
plugins:
  - name: example
    settings:
      unexpected: true
steps:
  - log:
      message: unreachable
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PluginConfigurationError, match="does not declare a settings model"):
        WorkflowRunner(plugins=[ExamplePlugin()]).load(workflow_path)
