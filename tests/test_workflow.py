from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from crawlerflow.engine.runner import WorkflowRunner
from crawlerflow.workflow.loader import WorkflowLoader, WorkflowLoadError


def test_loads_version_one_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: test
steps:
  - log:
      message: hello
""".strip(),
        encoding="utf-8",
    )

    workflow = WorkflowRunner().load(workflow_path)

    assert workflow.workflow.name == "test"
    assert workflow.steps[0].name == "log"


def test_rejects_unknown_step(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: test
steps:
  - missing: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown step"):
        WorkflowRunner().load(workflow_path)


def test_rejects_invalid_document(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text("version: 2", encoding="utf-8")

    with pytest.raises(WorkflowLoadError):
        WorkflowLoader().load(workflow_path)


def test_rejects_semantically_invalid_static_step(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-extract
steps:
  - extract:
      selector: h1
      text: true
      html: true
      save_as: title
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_executes_file_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: save-file
variables:
  greeting: hello
steps:
  - save_text:
      path: output/result.txt
      content: "{{greeting|upper}}"
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    assert context.workflow_name == "save-file"
    assert (tmp_path / "output" / "result.txt").read_text(encoding="utf-8") == "HELLO"


@pytest.mark.asyncio
async def test_extract_javascript_array_returns_browser_free_declaration(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "extract-javascript-array.yaml"
    workflow_path.write_text(
        r"""
version: 1
workflow:
  name: extract-javascript-array
steps:
  - extract_javascript_array:
      content: >-
        <script>
        var locations = [
          {name: "A ] item", coordinates: [37.1, 38.2]},
          /* a nested array */
          {name: 'B', coordinates: [39.3, 40.4]}
        ];
        </script>
      variable: locations
      declaration_kind: const
      save_as: locations_javascript
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    javascript = context.outputs["locations_javascript"]
    assert javascript.startswith("const locations = [")
    assert 'name: "A ] item"' in javascript
    assert "coordinates: [39.3, 40.4]" in javascript
    assert javascript.endswith("];\n")
    assert context.browser is None


@pytest.mark.asyncio
async def test_sanitizes_slashes_in_dynamic_output_filenames(tmp_path: Path) -> None:
    workflow_path = tmp_path / "safe-filename.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: safe-filename
variables:
  output_directory: output/reports
  district: MERKEZ/KÖY
steps:
  - save_text:
      path: "{{output_directory}}/{{district}}.txt"
      content: safe
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "reports" / "MERKEZ-KÖY.txt").read_text() == "safe"
    assert not (tmp_path / "output" / "reports" / "MERKEZ" / "KÖY.txt").exists()


@pytest.mark.asyncio
async def test_exposes_runtime_date_variables(tmp_path: Path) -> None:
    workflow_path = tmp_path / "runtime-date.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: runtime-date
steps:
  - save_text:
      path: runtime-month.txt
      content: "{{today|date('%Y-%m')}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    expected_month = datetime.now().astimezone().strftime("%Y-%m")
    assert (tmp_path / "runtime-month.txt").read_text(encoding="utf-8") == expected_month


@pytest.mark.asyncio
async def test_http_request_exposes_parsed_and_raw_response_bodies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_body = '{"error":0,"ilceler":[{"ilce":"Yalova"}]}'

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = raw_body

        @staticmethod
        def json() -> dict[str, object]:
            return {"error": 0, "ilceler": [{"ilce": "Yalova"}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def request(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(
        "crawlerflow.steps.utility.httpx.AsyncClient",
        FakeAsyncClient,
    )
    workflow_path = tmp_path / "http-json.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: http-json
steps:
  - http_request:
      url: https://example.com/api
      save_as: response
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    assert context.outputs["response"]["body"]["ilceler"][0]["ilce"] == "Yalova"
    assert context.outputs["response"]["raw_body"] == raw_body


@pytest.mark.asyncio
async def test_resolve_location_url_follows_redirects_and_extracts_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_options: list[dict[str, object]] = []
    request_headers: list[dict[str, str]] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html"}

        def __init__(self, url: str, text: str, redirect_count: int) -> None:
            self.url = url
            self.text = text
            self.history = [object()] * redirect_count

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            client_options.append(kwargs)

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> FakeResponse:
            assert method == "GET"
            headers = kwargs["headers"]
            assert isinstance(headers, dict)
            request_headers.append(headers)
            if url.endswith("/from-url"):
                return FakeResponse(
                    "https://www.google.com/maps/place/Test/"
                    "data=!4m2!3m1!1s0!8m2!3d37.7583262!4d39.3253490",
                    "<html></html>",
                    2,
                )
            return FakeResponse(
                "https://www.google.com/maps/place/Test",
                r'<link href="https:\/\/www.google.com\/maps\/@40.912987,29.202934,17z">',
                1,
            )

    monkeypatch.setattr(
        "crawlerflow.steps.utility.httpx.AsyncClient",
        FakeAsyncClient,
    )
    workflow_path = tmp_path / "resolve-location.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: resolve-location
steps:
  - resolve_location_url:
      url: https://goo.gl/maps/from-url
      max_redirects: 5
      save_as: url_location
  - resolve_location_url:
      url: https://goo.gl/maps/from-body
      headers:
        Accept-Language: tr-TR
      save_as: body_location
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    assert client_options[0]["follow_redirects"] is True
    assert client_options[0]["max_redirects"] == 5
    assert request_headers[0]["User-Agent"].startswith("Mozilla/5.0")
    assert request_headers[1]["Accept-Language"] == "tr-TR"
    assert context.outputs["url_location"] == {
        "original_url": "https://goo.gl/maps/from-url",
        "final_url": (
            "https://www.google.com/maps/place/Test/"
            "data=!4m2!3m1!1s0!8m2!3d37.7583262!4d39.3253490"
        ),
        "status_code": 200,
        "redirect_count": 2,
        "latitude": 37.7583262,
        "longitude": 39.325349,
        "source": "final_url",
    }
    assert context.outputs["body_location"]["latitude"] == 40.912987
    assert context.outputs["body_location"]["longitude"] == 29.202934
    assert context.outputs["body_location"]["source"] == "response_body"
    assert context.browser is None


@pytest.mark.asyncio
async def test_enrich_json_map_locations_adds_coordinates_to_item_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        history = [object()]

        def __init__(self, url: str, text: str = "<html></html>") -> None:
            self.url = url
            self.text = text

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["follow_redirects"] is True

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> FakeResponse:
            assert method == "GET"
            requested_urls.append(url)
            if url.endswith("/one"):
                return FakeResponse(
                    "https://www.google.com/maps/place/One/"
                    "data=!8m2!3d38.674228!4d29.405882"
                )
            if url.endswith("/two"):
                return FakeResponse(
                    "https://www.google.com/maps/place/Two",
                    '<meta content="https://www.google.com/maps/@38.6801,29.4102,17z">',
                )
            return FakeResponse("https://www.google.com/maps/place/Unknown")

    monkeypatch.setattr(
        "crawlerflow.steps.utility.httpx.AsyncClient",
        FakeAsyncClient,
    )
    workflow_path = tmp_path / "enrich-json-locations.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: enrich-json-locations
steps:
  - enrich_json_map_locations:
      data:
        - item:
            name: One
            map: https://goo.gl/maps/one
        - item:
            - name: One duplicate
              map: https://goo.gl/maps/one
            - name: Two
              map: https://goo.gl/maps/two
            - name: No map
            - name: Unknown location
              map: https://goo.gl/maps/unknown
      items_path: item
      url_field: map
      final_url_field: resolved_map
      parallel: true
      concurrency: 2
      on_url_error: continue
      save_as: enriched
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    enriched = context.outputs["enriched"]
    first = enriched[0]["item"]
    duplicate, second, no_map, unknown = enriched[1]["item"]
    assert requested_urls == [
        "https://goo.gl/maps/one",
        "https://goo.gl/maps/two",
        "https://goo.gl/maps/unknown",
    ]
    assert first["latitude"] == 38.674228
    assert first["longitude"] == 29.405882
    assert duplicate["latitude"] == first["latitude"]
    assert duplicate["longitude"] == first["longitude"]
    assert second["latitude"] == 38.6801
    assert second["longitude"] == 29.4102
    assert second["resolved_map"] == "https://www.google.com/maps/place/Two"
    assert "latitude" not in no_map
    assert "latitude" not in unknown
    assert context.storage["json_location_enrichment_errors"][0]["url"] == (
        "https://goo.gl/maps/unknown"
    )
    assert context.browser is None


@pytest.mark.asyncio
async def test_http_enrich_extracts_javascript_location_and_injects_parent_cards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<script>new Placemark([37.7583262,39.3253490 ]);</script>"

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> FakeResponse:
            assert method == "GET"
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(
        "crawlerflow.steps.utility.httpx.AsyncClient",
        FakeAsyncClient,
    )
    workflow_path = tmp_path / "http-enrich.yaml"
    workflow_path.write_text(
        r"""
version: 1
workflow:
  name: http-enrich
steps:
  - enrich_html_links_http:
      content: >-
        <section><div class="card">
        <a href="/detail/one">One</a></div>
        <div class="card"><a href="/detail/one">Again</a></div>
        <div class="card"><a href="/other">Ignored</a></div></section>
      base_url: https://example.com/list
      parent_selector: .card
      href_contains: /detail/
      detail_regex: >-
        (?x)Placemark\(\[\s*(?P<latitude>-?\d+(?:\.\d+)?)\s*,
        \s*(?P<longitude>-?\d+(?:\.\d+)?)\s*\]
      detail_template: '<div>Location: {latitude},{longitude}</div>'
      wrapper_class: location-detail
      save_as: enriched_html
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    enriched_html = context.outputs["enriched_html"]
    assert requested_urls == ["https://example.com/detail/one"]
    assert enriched_html.count("Location: 37.7583262,39.3253490") == 2
    assert enriched_html.count('class="location-detail"') == 2
    assert '<a href="/other">Ignored</a></div>' in enriched_html
    assert context.browser is None
