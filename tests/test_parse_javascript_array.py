from __future__ import annotations

from pathlib import Path

import pytest

from crawlerflow.engine.runner import WorkflowRunner


@pytest.mark.asyncio
async def test_parse_javascript_array_maps_rows_to_named_fields(tmp_path: Path) -> None:
    workflow_path = tmp_path / "parse-javascript-array.yaml"
    workflow_path.write_text(
        r'''
version: 1
workflow:
  name: parse-javascript-array
steps:
  - parse_javascript_array:
      content: >-
        var locations = [
          ['<h4>SEYİT ECZANESİ(NİZİP)</h4><span>Telefon</span>', 37.008961, 37.800632, 82630],
          // The parser accepts comments and trailing commas.
        ];
      variable: locations
      fields:
        - content
        - latitude
        - longitude
        - id
      save_as: locations
  - save_json:
      path: output/locations.json
      data: "{{locations}}"
'''.strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    assert context.outputs["locations"] == [
        {
            "content": "<h4>SEYİT ECZANESİ(NİZİP)</h4><span>Telefon</span>",
            "latitude": 37.008961,
            "longitude": 37.800632,
            "id": 82630,
        }
    ]
