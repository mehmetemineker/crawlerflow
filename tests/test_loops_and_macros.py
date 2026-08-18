from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crawlerflow.browser import BrowserAdapter, BrowserResponse
from crawlerflow.engine.executor import WorkflowExecutionError
from crawlerflow.engine.runner import WorkflowRunner


class SelectBrowser(BrowserAdapter):
    def __init__(self) -> None:
        self.selected: list[tuple[str, str]] = []
        self.closed = False

    async def goto(self, url: str) -> None:
        return None

    async def click(self, selector: str) -> None:
        return None

    async def fill(self, selector: str, value: str) -> None:
        return None

    async def select(self, selector: str, value: str) -> None:
        self.selected.append((selector, value))

    async def wait(self, selector: str, timeout_seconds: float | None = None) -> None:
        return None

    async def wait_network(self, timeout_seconds: float | None = None) -> None:
        return None

    async def html(self) -> str:
        return ""

    async def evaluate(self, script: str) -> Any:
        assert "HTMLSelectElement" in script
        return [
            {"value": "", "text": "Choose", "disabled": False, "index": 0},
            {"value": "34", "text": "Istanbul", "disabled": False, "index": 1},
            {"value": "35", "text": "Izmir", "disabled": True, "index": 2},
            {"value": "6", "text": "Ankara", "disabled": False, "index": 3},
        ]

    async def cookies(self) -> dict[str, str]:
        return {}

    async def set_cookies(self, cookies: dict[str, str]) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> BrowserResponse:
        return BrowserResponse(status_code=200)

    async def download(self, url: str, path: Path) -> Path:
        return path

    async def screenshot(self, path: Path) -> Path:
        return path

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_foreach_date_iterates_inclusive_range(tmp_path: Path) -> None:
    workflow_path = tmp_path / "dates.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: dates
steps:
  - foreach_date:
      start: 2026-08-01
      end: 2026-08-05
      step_days: 2
      steps:
        - save_text:
            path: "output/{{loop.date|date('%Y-%m-%d')}}.txt"
            content: "{{loop.index}}/{{loop.length}} {{loop.iso}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "2026-08-01.txt").read_text() == "1/3 2026-08-01"
    assert (tmp_path / "output" / "2026-08-03.txt").read_text() == "2/3 2026-08-03"
    assert (tmp_path / "output" / "2026-08-05.txt").read_text() == "3/3 2026-08-05"


@pytest.mark.asyncio
async def test_foreach_date_supports_single_date(tmp_path: Path) -> None:
    workflow_path = tmp_path / "single-date.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: single-date
steps:
  - foreach_date:
      start: 2026-08-05
      as: report_date
      steps:
        - save_text:
            path: result.txt
            content: "{{loop.report_date|date('%d.%m.%Y')}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "result.txt").read_text() == "05.08.2026"


def test_foreach_date_rejects_reverse_range(tmp_path: Path) -> None:
    workflow_path = tmp_path / "reverse-date.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: reverse-date
steps:
  - foreach_date:
      start: 2026-08-05
      end: 2026-08-01
      steps:
        - log:
            message: test
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="end must be on or after start"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_foreach_select_filters_and_selects_options(tmp_path: Path) -> None:
    workflow_path = tmp_path / "select.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: select-options
steps:
  - foreach_select:
      selector: "#city"
      as: city
      include_empty: false
      steps:
        - save_text:
            path: "output/{{loop.city}}.txt"
            content: "{{loop.text}} at {{loop.option_index}}"
""".strip(),
        encoding="utf-8",
    )
    browser = SelectBrowser()

    await WorkflowRunner(browser=browser).run(workflow_path)

    assert browser.selected == [("#city", "34"), ("#city", "6")]
    assert (tmp_path / "output" / "34.txt").read_text() == "Istanbul at 1"
    assert (tmp_path / "output" / "6.txt").read_text() == "Ankara at 3"
    assert browser.closed is True


@pytest.mark.asyncio
async def test_foreach_select_excludes_configured_values(tmp_path: Path) -> None:
    workflow_path = tmp_path / "select-exclusions.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: select-exclusions
steps:
  - foreach_select:
      selector: "#city"
      include_empty: false
      exclude_values: 6
      steps:
        - log:
            message: "{{loop.text}}"
""".strip(),
        encoding="utf-8",
    )
    browser = SelectBrowser()

    await WorkflowRunner(browser=browser).run(workflow_path)

    assert browser.selected == [("#city", "34")]
    assert browser.closed is True


@pytest.mark.asyncio
async def test_foreach_select_overrides_text_by_option_value(tmp_path: Path) -> None:
    workflow_path = tmp_path / "select-text-overrides.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: select-text-overrides
steps:
  - foreach_select:
      selector: "#city"
      include_empty: false
      text_overrides:
        34: Türkiye - {original_text}
        "6": "{original_text} - Türkiye ({value})"
      steps:
        - save_text:
            path: "output/{{loop.text}}.txt"
            content: "{{loop.value}}: {{loop.original_text}}"
""".strip(),
        encoding="utf-8",
    )
    browser = SelectBrowser()

    await WorkflowRunner(browser=browser).run(workflow_path)

    assert (tmp_path / "output" / "Türkiye - Istanbul.txt").read_text(
        encoding="utf-8"
    ) == "34: Istanbul"
    assert (tmp_path / "output" / "Ankara - Türkiye (6).txt").read_text(
        encoding="utf-8"
    ) == "6: Ankara"
    assert browser.selected == [("#city", "34"), ("#city", "6")]


@pytest.mark.asyncio
async def test_foreach_select_applies_one_text_override_to_multiple_values(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "grouped-select-text-overrides.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: grouped-select-text-overrides
steps:
  - foreach_select:
      selector: "#city"
      include_empty: false
      text_overrides:
        - values: [34, "6"]
          text: "Türkiye - {original_text}"
      steps:
        - save_text:
            path: "output/{{loop.value}}.txt"
            content: "{{loop.text}}"
""".strip(),
        encoding="utf-8",
    )
    browser = SelectBrowser()

    await WorkflowRunner(browser=browser).run(workflow_path)

    assert (tmp_path / "output" / "34.txt").read_text(encoding="utf-8") == (
        "Türkiye - Istanbul"
    )
    assert (tmp_path / "output" / "6.txt").read_text(encoding="utf-8") == (
        "Türkiye - Ankara"
    )


@pytest.mark.asyncio
async def test_foreach_select_reads_options_from_supplied_html_without_browser(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "select-from-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: select-options-from-html
steps:
  - foreach_select:
      content: >-
        <select name="ilce"><option value="999">Ignored</option></select>
        <form name="uye"><select class="form-control" name="ilce">
        <option value="">Choose</option>
        <option value="2085" selected>MERKEZEFENDİ</option>
        <option value="2084" disabled>PAMUKKALE</option></select></form>
      selector: 'form[name="uye"] select[name="ilce"]'
      as: district
      include_empty: false
      steps:
        - save_text:
            path: "output/{{loop.value}}.txt"
            content: "{{loop.text}} at {{loop.option_index}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "2085.txt").read_text(encoding="utf-8") == (
        "MERKEZEFENDİ at 1"
    )
    assert not (tmp_path / "output" / "2084.txt").exists()


@pytest.mark.asyncio
async def test_foreach_select_runs_supplied_html_options_in_parallel(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "parallel-select-from-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: parallel-select-options-from-html
steps:
  - foreach_select:
      content: >-
        <select name="ilce"><option value="1">One</option>
        <option value="2">Two</option></select>
      selector: 'select[name="ilce"]'
      parallel: true
      concurrency: 2
      steps:
        - save_text:
            path: "output/{{loop.value}}.txt"
            content: "{{loop.text}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "1.txt").read_text() == "One"
    assert (tmp_path / "output" / "2.txt").read_text() == "Two"


@pytest.mark.asyncio
async def test_macro_receives_arguments_inside_foreach(tmp_path: Path) -> None:
    workflow_path = tmp_path / "macro.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: macro
variables:
  cities: [istanbul, ankara]
macros:
  save_city:
    - save_text:
        path: "output/{{macro.filename}}"
        content: "{{macro.content|upper}}"
steps:
  - foreach:
      items: "{{cities}}"
      as: city
      steps:
        - run_macro:
            name: save_city
            with:
              filename: "{{loop.city}}.txt"
              content: "{{loop.city}}"
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    assert (tmp_path / "output" / "istanbul.txt").read_text() == "ISTANBUL"
    assert (tmp_path / "output" / "ankara.txt").read_text() == "ANKARA"


def test_validation_rejects_unknown_macro(tmp_path: Path) -> None:
    workflow_path = tmp_path / "unknown-macro.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: unknown-macro
steps:
  - run_macro:
      name: missing
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown macro: missing"):
        WorkflowRunner().load(workflow_path)


def test_validation_rejects_invalid_step_inside_macro(tmp_path: Path) -> None:
    workflow_path = tmp_path / "invalid-macro.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-macro
macros:
  broken:
    - missing: {}
steps:
  - run_macro:
      name: broken
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown step: missing"):
        WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_macro_recursion_is_rejected(tmp_path: Path) -> None:
    workflow_path = tmp_path / "recursive-macro.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: recursive-macro
macros:
  first:
    - run_macro:
        name: second
  second:
    - run_macro:
        name: first
steps:
  - run_macro:
      name: first
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowExecutionError, match="first -> second -> first"):
        await WorkflowRunner().run(workflow_path)
