from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.executor import WorkflowExecutionError
from crawlerflow.engine.registry import BaseStep, StepRegistry
from crawlerflow.engine.runner import WorkflowRunner


class ParallelProbeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParallelProbeStep(BaseStep[ParallelProbeConfig]):
    config_model = ParallelProbeConfig
    active = 0
    peak_active = 0
    release: asyncio.Event | None = None

    @classmethod
    def reset(cls) -> None:
        cls.active = 0
        cls.peak_active = 0
        cls.release = asyncio.Event()

    async def execute(self, context: WorkflowContext) -> None:
        type(self).active += 1
        type(self).peak_active = max(type(self).peak_active, type(self).active)
        release = type(self).release
        if release is None:
            raise RuntimeError("Parallel probe is not initialized")
        if type(self).active >= 2:
            release.set()
        try:
            await asyncio.wait_for(release.wait(), timeout=1)
            loop = context.expression_variables()["loop"]
            context.outputs["parallel_result"] = loop["item"]
        finally:
            type(self).active -= 1


def create_parallel_registry() -> StepRegistry:
    registry = StepRegistry()
    registry.register("parallel_probe", ParallelProbeStep)
    return registry


@pytest.mark.asyncio
async def test_foreach_exposes_item_and_loop_metadata(tmp_path: Path) -> None:
    workflow_path = tmp_path / "foreach.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: foreach-list
variables:
  cities: [istanbul, ankara]
steps:
  - foreach:
      items: "{{cities}}"
      as: city
      steps:
        - save_text:
            path: "output/{{loop.city}}.txt"
            content: "{{loop.index}}/{{loop.length}} {{loop.city|upper}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "istanbul.txt").read_text() == "1/2 ISTANBUL"
    assert (tmp_path / "output" / "ankara.txt").read_text() == "2/2 ANKARA"


@pytest.mark.asyncio
async def test_foreach_mapping_exposes_key_and_value(tmp_path: Path) -> None:
    workflow_path = tmp_path / "foreach-mapping.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: foreach-mapping
variables:
  codes:
    istanbul: 34
    ankara: 6
steps:
  - foreach:
      items: "{{codes}}"
      steps:
        - save_text:
            path: "output/{{loop.key}}.txt"
            content: "{{loop.value}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "istanbul.txt").read_text() == "34"
    assert (tmp_path / "output" / "ankara.txt").read_text() == "6"


@pytest.mark.asyncio
async def test_foreach_runs_iterations_in_parallel_with_concurrency_limit(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "parallel-foreach.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: parallel-foreach
steps:
  - foreach:
      items: [one, two, three]
      parallel: true
      concurrency: 2
      steps:
        - parallel_probe: {}
        - save_text:
            path: "output/{{loop.item}}.txt"
            content: "{{parallel_result}}"
""".strip(),
        encoding="utf-8",
    )
    ParallelProbeStep.reset()

    context = await WorkflowRunner(registry=create_parallel_registry()).run(workflow_path)

    assert ParallelProbeStep.peak_active == 2
    assert context.outputs["parallel_result"] == "three"
    for item in ("one", "two", "three"):
        assert (tmp_path / "output" / f"{item}.txt").read_text() == item


def test_loop_concurrency_requires_parallel_mode(tmp_path: Path) -> None:
    workflow_path = tmp_path / "invalid-loop-concurrency.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-loop-concurrency
steps:
  - foreach:
      items: [one]
      concurrency: 2
      steps:
        - log:
            message: test
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="concurrency requires parallel"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_if_executes_matching_comparison_branch(tmp_path: Path) -> None:
    workflow_path = tmp_path / "if.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: if-comparison
variables:
  score: 12
steps:
  - if:
      condition:
        left: "{{score}}"
        operator: gte
        right: 10
      then:
        - save_text:
            path: result.txt
            content: passed
      else:
        - save_text:
            path: result.txt
            content: failed
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "result.txt").read_text() == "passed"


@pytest.mark.asyncio
async def test_if_accepts_boolean_expression(tmp_path: Path) -> None:
    workflow_path = tmp_path / "if-boolean.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: if-boolean
variables:
  enabled: false
steps:
  - if:
      condition: "{{enabled}}"
      then:
        - save_text:
            path: result.txt
            content: enabled
      else:
        - save_text:
            path: result.txt
            content: disabled
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "result.txt").read_text() == "disabled"


@pytest.mark.asyncio
async def test_nested_foreach_exposes_parent_loop(tmp_path: Path) -> None:
    workflow_path = tmp_path / "nested.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: nested-foreach
variables:
  regions:
    - name: marmara
      cities: [istanbul, bursa]
steps:
  - foreach:
      items: "{{regions}}"
      as: region
      steps:
        - foreach:
            items: "{{loop.region.cities}}"
            as: city
            steps:
              - save_text:
                  path: "output/{{loop.parent.region.name}}-{{loop.city}}.txt"
                  content: "{{loop.city}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "marmara-istanbul.txt").exists()
    assert (tmp_path / "output" / "marmara-bursa.txt").exists()


def test_validation_rejects_unknown_nested_step(tmp_path: Path) -> None:
    workflow_path = tmp_path / "invalid-nested.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-nested
variables:
  items: [one]
steps:
  - foreach:
      items: "{{items}}"
      steps:
        - missing: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown step: missing"):
        WorkflowRunner().load(workflow_path)


def test_validation_rejects_reserved_loop_alias(tmp_path: Path) -> None:
    workflow_path = tmp_path / "reserved-alias.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: reserved-alias
steps:
  - foreach:
      items: [one]
      as: index
      steps:
        - log:
            message: test
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reserved loop metadata"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_nested_failure_reports_full_step_path(tmp_path: Path) -> None:
    workflow_path = tmp_path / "nested-error.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: nested-error
variables:
  items: [one]
  delay: -1
steps:
  - foreach:
      items: "{{items}}"
      steps:
        - sleep:
            seconds: "{{delay}}"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowExecutionError, match=r"Step 1\.1 \('sleep'\) failed"):
        await WorkflowRunner().run(workflow_path)
