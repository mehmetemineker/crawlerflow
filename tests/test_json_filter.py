from __future__ import annotations

import json
from pathlib import Path

import pytest

from crawlerflow.engine.runner import WorkflowRunner


@pytest.mark.asyncio
async def test_filter_json_filters_nested_items_and_selects_fields(tmp_path: Path) -> None:
    workflow_path = tmp_path / "filter-json.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: filter-json
steps:
  - filter_json:
      data:
        meta:
          source: test
        items:
          - id: 1
            name: active one
            status: active
            private: secret-1
          - id: 2
            name: inactive
            status: inactive
            private: secret-2
      items_path: items
      where:
        - path: status
          operator: eq
          value: active
      select:
        id: id
        label: name
      save_as: filtered
  - save_json:
      path: output/result.json
      data: "{{filtered}}"
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    assert context.outputs["filtered"] == {
        "meta": {"source": "test"},
        "items": [{"id": 1, "label": "active one"}],
    }
    assert json.loads((tmp_path / "output" / "result.json").read_text()) == (
        context.outputs["filtered"]
    )


@pytest.mark.asyncio
async def test_filter_json_supports_root_object_projection(tmp_path: Path) -> None:
    workflow_path = tmp_path / "filter-object.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: filter-object
steps:
  - filter_json:
      data:
        id: 7
        profile:
          name: Mehmet
        internal: hidden
      select:
        id: id
        display_name: profile.name
      save_as: filtered
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    assert context.outputs["filtered"] == {"id": 7, "display_name": "Mehmet"}


def test_filter_json_rejects_invalid_items_path(tmp_path: Path) -> None:
    workflow_path = tmp_path / "invalid-filter-json.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-filter-json
steps:
  - filter_json:
      data: []
      items_path: items..data
      save_as: filtered
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="empty segment"):
        WorkflowRunner().load(workflow_path)
