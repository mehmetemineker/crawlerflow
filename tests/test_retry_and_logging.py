from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from crawlerflow.browser import BrowserResponse
from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.executor import WorkflowExecutionError
from crawlerflow.engine.registry import BaseStep, StepRegistry
from crawlerflow.engine.runner import WorkflowRunner
from crawlerflow.events import EventBus, EventName, WorkflowEvent


class FlakyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: int = Field(ge=0)


class FlakyStep(BaseStep[FlakyConfig]):
    config_model = FlakyConfig

    async def execute(self, context: WorkflowContext) -> str:
        attempts = int(context.storage.get("flaky_attempts", 0)) + 1
        context.storage["flaky_attempts"] = attempts
        if attempts <= self.config.failures:
            raise RuntimeError(f"failure {attempts}")
        return "recovered"


class RequestBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> BrowserResponse:
        return BrowserResponse(status_code=201, headers={"x-test": "yes"}, body="created")

    async def close(self) -> None:
        self.closed = True


def create_registry() -> StepRegistry:
    registry = StepRegistry()
    registry.register("flaky", FlakyStep)
    return registry


@pytest.mark.asyncio
async def test_retry_recovers_and_emits_attempt_events(tmp_path: Path) -> None:
    workflow_path = tmp_path / "retry.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: retry-success
steps:
  - flaky:
      failures: 2
    retry:
      attempts: 3
      delay: 0
      backoff: 2
""".strip(),
        encoding="utf-8",
    )
    events: list[WorkflowEvent] = []
    event_bus = EventBus()
    event_bus.subscribe_all(events.append)

    context = await WorkflowRunner(registry=create_registry(), event_bus=event_bus).run(
        workflow_path
    )

    assert context.storage["flaky_attempts"] == 3
    assert context.storage["last_step_result"] == "recovered"
    retry_started = [event for event in events if event.name is EventName.RETRY_STARTED]
    retry_finished = [event for event in events if event.name is EventName.RETRY_FINISHED]
    step_finished = next(event for event in events if event.name is EventName.STEP_FINISHED)
    assert [event.payload["attempt"] for event in retry_started] == [2, 3]
    assert [event.payload["succeeded"] for event in retry_finished] == [False, True]
    assert step_finished.payload["attempts"] == 3
    assert step_finished.payload["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_on_error_continue_saves_structured_error(tmp_path: Path) -> None:
    workflow_path = tmp_path / "continue.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: continue-after-error
steps:
  - flaky:
      failures: 5
    retry:
      attempts: 2
    on_error: continue
    save_error_as: failure
  - save_text:
      path: result.txt
      content: "{{failure.error_type}}:{{failure.failed_step}}"
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner(registry=create_registry()).run(workflow_path)

    assert context.storage["flaky_attempts"] == 2
    assert context.outputs["failure"]["error_type"] == "RuntimeError"
    assert (tmp_path / "result.txt").read_text() == "RuntimeError:flaky"


def test_invalid_retry_policy_is_rejected_before_run(tmp_path: Path) -> None:
    workflow_path = tmp_path / "invalid-retry.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-retry
steps:
  - log:
      message: test
    retry:
      attempts: 0
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="greater than or equal to 1"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_json_logger_writes_lifecycle_and_detaches(tmp_path: Path) -> None:
    workflow_path = tmp_path / "logging.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: logging
logging:
  path: logs/workflow.jsonl
  mode: overwrite
steps:
  - log:
      message: hello
""".strip(),
        encoding="utf-8",
    )
    runner = WorkflowRunner()

    await runner.run(workflow_path)
    first_run = _read_json_lines(tmp_path / "logs" / "workflow.jsonl")
    await runner.run(workflow_path)
    second_run = _read_json_lines(tmp_path / "logs" / "workflow.jsonl")

    assert len(second_run) == len(first_run)
    assert second_run[0]["event"] == "workflow.started"
    assert second_run[-1]["event"] == "workflow.finished"
    assert second_run[-1]["payload"]["status"] == "succeeded"
    finished_step = next(record for record in second_run if record["event"] == "step.finished")
    assert finished_step["payload"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_console_logger_automatically_logs_nested_steps_and_duration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow_path = tmp_path / "console-logging.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: console-logging
logging:
  console: true
steps:
  - if:
      condition: true
      then:
        - sleep:
            seconds: 0
""".strip(),
        encoding="utf-8",
    )
    runner = WorkflowRunner()

    await runner.run(workflow_path)
    first_run = capsys.readouterr().out
    await runner.run(workflow_path)
    second_run = capsys.readouterr().out

    for output in (first_run, second_run):
        assert output.count("[step 1] STARTED if") == 1
        assert output.count("[step 1.1] STARTED sleep") == 1
        assert "[step 1.1] SUCCEEDED sleep (" in output
        assert "[step 1] SUCCEEDED if (" in output
        assert " ms)" in output


@pytest.mark.asyncio
async def test_json_logger_records_failed_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "failed-logging.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: failed-logging
logging:
  path: workflow.jsonl
  mode: overwrite
steps:
  - flaky:
      failures: 1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowExecutionError):
        await WorkflowRunner(registry=create_registry()).run(workflow_path)

    records = _read_json_lines(tmp_path / "workflow.jsonl")
    assert records[-1]["event"] == "workflow.finished"
    assert records[-1]["payload"]["status"] == "failed"


@pytest.mark.asyncio
async def test_browser_request_emits_request_events(tmp_path: Path) -> None:
    workflow_path = tmp_path / "request.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: request-events
steps:
  - browser_request:
      method: POST
      url: https://example.com/api
""".strip(),
        encoding="utf-8",
    )
    browser = RequestBrowser()
    events: list[WorkflowEvent] = []
    event_bus = EventBus()
    event_bus.subscribe_all(events.append)

    await WorkflowRunner(browser=browser, event_bus=event_bus).run(workflow_path)

    request_events = [
        event
        for event in events
        if event.name in {EventName.REQUEST_STARTED, EventName.REQUEST_FINISHED}
    ]
    assert [event.name for event in request_events] == [
        EventName.REQUEST_STARTED,
        EventName.REQUEST_FINISHED,
    ]
    assert request_events[-1].payload["status_code"] == 201
    assert request_events[-1].payload["transport"] == "browser"
    assert browser.closed is True


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]
