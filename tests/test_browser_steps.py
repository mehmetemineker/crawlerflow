from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crawlerflow.browser import BrowserAdapter, BrowserResponse
from crawlerflow.engine.runner import WorkflowRunner


class FakeBrowser(BrowserAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    async def goto(self, url: str) -> None:
        self.calls.append(("goto", url))

    async def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    async def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", (selector, value)))

    async def select(self, selector: str, value: str) -> None:
        self.calls.append(("select", (selector, value)))

    async def wait(self, selector: str, timeout_seconds: float | None = None) -> None:
        self.calls.append(("wait", (selector, timeout_seconds)))

    async def wait_network(self, timeout_seconds: float | None = None) -> None:
        self.calls.append(("wait_network", timeout_seconds))

    async def html(self) -> str:
        self.calls.append(("html", None))
        return "<html><h1>Example</h1></html>"

    async def evaluate(self, script: str) -> Any:
        self.calls.append(("evaluate", script))
        if "const linkSource" in script:
            return [
                {"index": 0, "url": "https://example.com/detail/one"},
                {"index": 1, "url": "https://example.com/detail/one"},
                {"index": 2, "url": "https://example.com/detail/two"},
            ]
        if "const detailSource" in script:
            return {"ready": True, "html": '<section class="detail">Detail</section>'}
        if "const parentSource" in script:
            return (
                '<table><tr><td><a class="item">One</a>'
                '<div class="item-detail">Detail</div></td></tr></table>'
            )
        if "const formatSource" in script:
            return '<main>\n  <h1>Title</h1>\n  <p>Content</p>\n</main>'
        if "new DOMParser()" in script and ".missing" in script:
            return " \n"
        if "new DOMParser()" in script:
            return '<main id="result">Result</main>\n<p class="notice">Notice</p>'
        return "Example"

    async def cookies(self) -> dict[str, str]:
        self.calls.append(("cookies", None))
        return {"session": "abc"}

    async def set_cookies(self, cookies: dict[str, str]) -> None:
        self.calls.append(("set_cookies", cookies))

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> BrowserResponse:
        self.calls.append(("request", (method, url, headers, data)))
        return BrowserResponse(
            status_code=200,
            body=f'<html><main><section class="detail">{url}</section></main></html>',
        )

    async def download(self, url: str, path: Path) -> Path:
        self.calls.append(("download", (url, path)))
        return path

    async def screenshot(self, path: Path) -> Path:
        self.calls.append(("screenshot", path))
        return path

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_browser_steps_use_injected_adapter(tmp_path: Path) -> None:
    workflow_path = tmp_path / "browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: browser-test
variables:
  target: https://example.com
steps:
  - goto:
      url: "{{target}}"
  - extract:
      selector: h1
      text: true
      save_as: title
  - save_text:
      path: title.txt
      content: "{{title}}"
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    assert browser.calls[0] == ("goto", "https://example.com")
    assert context.outputs["title"] == "Example"
    assert (tmp_path / "title.txt").read_text(encoding="utf-8") == "Example"
    assert browser.closed is True


def test_validation_allows_expression_for_typed_field(tmp_path: Path) -> None:
    workflow_path = tmp_path / "sleep.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: expression-validation
variables:
  delay: 0
steps:
  - sleep:
      seconds: "{{delay}}"
""".strip(),
        encoding="utf-8",
    )

    WorkflowRunner().load(workflow_path)


@pytest.mark.asyncio
async def test_browser_artifact_and_cookie_steps(tmp_path: Path) -> None:
    workflow_path = tmp_path / "artifacts.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: browser-artifacts
steps:
  - get_cookies:
      save_as: session_cookies
  - set_cookies:
      cookies: "{{session_cookies}}"
  - download:
      url: https://example.com/report.csv
      path: downloads/report.csv
      save_as: downloaded_file
  - screenshot:
      path: screenshots/page.png
      save_as: screenshot_file
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    download_path = tmp_path / "downloads" / "report.csv"
    screenshot_path = tmp_path / "screenshots" / "page.png"
    assert ("cookies", None) in browser.calls
    assert ("set_cookies", {"session": "abc"}) in browser.calls
    assert ("download", ("https://example.com/report.csv", download_path)) in browser.calls
    assert ("screenshot", screenshot_path) in browser.calls
    assert context.cookies == {"session": "abc"}
    assert context.outputs["downloaded_file"] == str(download_path)
    assert context.outputs["screenshot_file"] == str(screenshot_path)


@pytest.mark.asyncio
async def test_save_html_without_selectors_saves_entire_browser_html(tmp_path: Path) -> None:
    workflow_path = tmp_path / "save-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: save-full-html
steps:
  - save_html:
      path: full.html
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    assert (tmp_path / "full.html").read_text(encoding="utf-8") == (
        "<html><h1>Example</h1></html>"
    )
    assert context.last_html == "<html><h1>Example</h1></html>"
    assert ("html", None) in browser.calls


@pytest.mark.asyncio
async def test_save_html_filters_supplied_content_with_multiple_selectors(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "save-selected-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: save-selected-html
steps:
  - save_html:
      path: selected.html
      content: >-
        <html><body>
        <main id="result">Result</main>
        <p class="notice">Notice</p>
        </body></html>
      selectors:
        - main
        - .notice
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    expected = '<main id="result">Result</main>\n<p class="notice">Notice</p>'
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected
    evaluation = next(
        value for name, value in browser.calls if name == "evaluate" and "DOMParser" in value
    )
    assert evaluation.startswith("(() => { ")
    assert evaluation.endswith("})()")
    assert '["main", ".notice"]' in evaluation
    assert ("html", None) not in browser.calls


def test_save_html_accepts_one_selector_as_string(tmp_path: Path) -> None:
    workflow_path = tmp_path / "save-selected-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: save-selected-html
steps:
  - save_html:
      path: selected.html
      selectors: main
""".strip(),
        encoding="utf-8",
    )

    workflow = WorkflowRunner().load(workflow_path)

    step = WorkflowRunner().registry.create("save_html", workflow.steps[0].config)
    assert step.config.selectors == ["main"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skip_if_empty", "file_exists"),
    [(True, False), (False, True)],
)
async def test_save_html_optionally_skips_empty_content(
    tmp_path: Path,
    skip_if_empty: bool,
    file_exists: bool,
) -> None:
    workflow_path = tmp_path / f"skip-empty-{skip_if_empty}.yaml"
    skip_setting = "\n      skip_if_empty: true" if skip_if_empty else ""
    workflow_path.write_text(
        f"""
version: 1
workflow:
  name: skip-empty-html
steps:
  - save_html:
      path: empty.html
      content: "<html><body></body></html>"
      selectors: .missing{skip_setting}
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    output_path = tmp_path / "empty.html"
    assert output_path.exists() is file_exists
    if file_exists:
        assert output_path.read_text(encoding="utf-8") == " \n"
    assert context.last_html == " \n"


@pytest.mark.asyncio
async def test_save_html_optionally_pretty_formats_content(tmp_path: Path) -> None:
    workflow_path = tmp_path / "pretty-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: pretty-html
steps:
  - save_html:
      path: pretty.html
      content: "<main><h1>Title</h1><p>Content</p></main>"
      pretty: true
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    expected = '<main>\n  <h1>Title</h1>\n  <p>Content</p>\n</main>'
    assert (tmp_path / "pretty.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected
    evaluation = next(
        value for name, value in browser.calls if name == "evaluate" and "formatSource" in value
    )
    assert evaluation.startswith("(() => { ")
    assert evaluation.endswith("})()")


@pytest.mark.asyncio
async def test_save_html_pretty_formats_without_browser_adapter(tmp_path: Path) -> None:
    workflow_path = tmp_path / "pretty-html-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: pretty-html-without-browser
steps:
  - save_html:
      path: pretty.html
      content: "<main><h1>Başlık</h1><p>İçerik</p></main>"
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = '<main>\n  <h1>Başlık</h1>\n  <p>İçerik</p>\n</main>'
    assert (tmp_path / "pretty.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_browser_free_pretty_formatter_preserves_svg_attribute_case(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "pretty-svg-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: pretty-svg-without-browser
steps:
  - save_html:
      path: pretty.html
      content: '<svg viewBox="0 0 10 10"><linearGradient id="a" /></svg>'
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    await WorkflowRunner().run(workflow_path)

    expected = '<svg viewBox="0 0 10 10">\n  <linearGradient id="a" />\n</svg>'
    assert (tmp_path / "pretty.html").read_text(encoding="utf-8") == expected


@pytest.mark.asyncio
async def test_save_html_filters_simple_css_selectors_without_browser(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "selected-html-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: selected-html-without-browser
steps:
  - save_html:
      path: selected.html
      content: >-
        <main><div class="listing first"><h2>One</h2></div>
        <p>Ignored</p><div class="listing"><h2>Two</h2></div></main>
      selectors: .listing
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = (
        '<div class="listing first">\n  <h2>One</h2>\n</div>\n'
        '<div class="listing">\n  <h2>Two</h2>\n</div>'
    )
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_save_html_filters_descendant_css_selectors_without_browser(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "selected-descendant-html-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: selected-descendant-html-without-browser
steps:
  - save_html:
      path: selected.html
      content: >-
        <main class="main-content sixteen columns">
        <section><div class="sixteen columns"><h2>Included</h2></div></section>
        </main><div class="sixteen columns"><h2>Ignored</h2></div>
      selectors: ".main-content .sixteen.columns"
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = '<div class="sixteen columns">\n  <h2>Included</h2>\n</div>'
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_save_html_preserves_declared_selector_order_without_browser(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "ordered-selected-html-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: ordered-selected-html-without-browser
steps:
  - save_html:
      path: selected.html
      content: >-
        <div class="content">Content</div><h1 class="title">Title</h1>
        <p class="content title">Shared</p>
      selectors:
        - .title
        - .content
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = (
        '<h1 class="title">Title</h1>\n'
        '<p class="content title">Shared</p>\n'
        '<div class="content">Content</div>'
    )
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_save_html_limits_browser_free_selector_matches(tmp_path: Path) -> None:
    workflow_path = tmp_path / "limited-selected-html-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: limited-selected-html-without-browser
steps:
  - save_html:
      path: selected.html
      content: >-
        <body><div class="outer"><div class="inner">Included child</div></div>
        <div class="ignored">Ignored sibling</div></body>
      selectors: "body div"
      limit: 1
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = (
        '<div class="outer">\n  <div class="inner">Included child</div>\n</div>'
    )
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_save_html_skips_browser_free_selector_matches(tmp_path: Path) -> None:
    workflow_path = tmp_path / "offset-selected-html-without-browser.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: offset-selected-html-without-browser
steps:
  - save_html:
      path: selected.html
      content: >-
        <body><div class="outer"><div class="header">Header</div>
        <div class="item">One</div><div class="item">Two</div></div></body>
      selectors: "body div"
      offset: 2
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = (
        '<div class="item">One</div>\n<div class="item">Two</div>'
    )
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_save_html_optionally_removes_comments(tmp_path: Path) -> None:
    workflow_path = tmp_path / "comment-free-html.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: comment-free-html
steps:
  - save_html:
      path: selected.html
      content: >-
        <!-- outside --><main><h1>Title</h1><!-- inside
        comment --><p>Content</p></main>
      selectors: main
      ignore_comments: true
      pretty: true
""".strip(),
        encoding="utf-8",
    )

    context = await WorkflowRunner().run(workflow_path)

    expected = "<main>\n  <h1>Title</h1>\n  <p>Content</p>\n</main>"
    assert (tmp_path / "selected.html").read_text(encoding="utf-8") == expected
    assert context.last_html == expected


@pytest.mark.asyncio
async def test_enrich_html_links_fetches_unique_pages_and_injects_each_link(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "enrich-links.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: enrich-links
steps:
  - enrich_html_links:
      content: >-
        <table><tr>
        <td><a class="item" href="/detail/one">One</a></td>
        <td><a class="item" href="/detail/one">One again</a></td>
        <td><a class="item" href="/detail/two">Two</a></td>
        </tr></table>
      base_url: https://example.com/list
      link_selector: a.item
      detail_selectors:
        - main .detail
      detail_wait_selector: main .detail
      timeout: 5
      wrapper_class: item-detail
      headers:
        Referer: https://example.com/list
      on_link_error: continue
      save_as: enriched_html
  - save_html:
      path: enriched.html
      content: "{{enriched_html}}"
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    context = await WorkflowRunner(browser=browser).run(workflow_path)

    requests = [value for name, value in browser.calls if name == "request"]
    assert requests == [
        (
            "GET",
            "https://example.com/detail/one",
            {"Referer": "https://example.com/list"},
            None,
        ),
        (
            "GET",
            "https://example.com/detail/two",
            {"Referer": "https://example.com/list"},
            None,
        ),
    ]
    expected = (
        '<table><tr><td><a class="item">One</a>'
        '<div class="item-detail">Detail</div></td></tr></table>'
    )
    assert context.outputs["enriched_html"] == expected
    assert context.last_html == expected
    assert (tmp_path / "enriched.html").read_text(encoding="utf-8") == expected

    evaluations = [value for name, value in browser.calls if name == "evaluate"]
    link_script = next(value for value in evaluations if "const linkSource" in value)
    detail_script = next(value for value in evaluations if "const detailSource" in value)
    injection_script = next(value for value in evaluations if "const parentSource" in value)
    assert link_script.startswith("(() => { ") and link_script.endswith("})()")
    assert detail_script.startswith("(() => { ") and detail_script.endswith("})()")
    assert 'main .detail' in detail_script
    assert 'const waitSelector = "main .detail"' in detail_script
    assert injection_script.startswith("(() => { ") and injection_script.endswith("})()")
    assert injection_script.count('https://example.com/detail/one') == 2
    assert 'item-detail' in injection_script
    assert 'data-crawlerflow-source' in injection_script


@pytest.mark.asyncio
async def test_enrich_html_links_times_out_when_detail_selector_never_appears(
    tmp_path: Path,
) -> None:
    class NeverReadyBrowser(FakeBrowser):
        async def evaluate(self, script: str) -> Any:
            if "const linkSource" in script:
                return [{"index": 0, "url": "https://example.com/detail/one"}]
            if "const detailSource" in script:
                return {"ready": False, "html": ""}
            return await super().evaluate(script)

    workflow_path = tmp_path / "detail-timeout.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: detail-timeout
steps:
  - enrich_html_links:
      content: '<a class="item" href="/detail/one">One</a>'
      base_url: https://example.com/list
      link_selector: a.item
      detail_selectors: main .detail
      detail_wait_selector: main .ready
      timeout: 0.001
      save_as: enriched_html
""".strip(),
        encoding="utf-8",
    )
    browser = NeverReadyBrowser()

    with pytest.raises(
        RuntimeError,
        match=r"Detail wait selector was not found within 0.001 seconds.*main \.ready",
    ):
        await WorkflowRunner(browser=browser).run(workflow_path)

    requests = [value for name, value in browser.calls if name == "request"]
    assert requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pacing_setting", "expected_interval"),
    [("delay: 0.2", 0.2), ("rate_limit: 4", 0.25)],
)
async def test_enrich_html_links_throttles_detail_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pacing_setting: str,
    expected_interval: float,
) -> None:
    sleep_calls: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("crawlerflow.steps.browser.asyncio.sleep", record_sleep)
    workflow_path = tmp_path / "detail-pacing.yaml"
    workflow_path.write_text(
        f"""
version: 1
workflow:
  name: detail-pacing
steps:
  - enrich_html_links:
      content: '<a class="item" href="/detail/one">One</a>'
      base_url: https://example.com/list
      link_selector: a.item
      detail_selectors: main .detail
      detail_wait_selector: main .detail
      {pacing_setting}
      save_as: enriched_html
""".strip(),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    await WorkflowRunner(browser=browser).run(workflow_path)

    assert len(sleep_calls) == 1
    assert expected_interval - 0.05 <= sleep_calls[0] <= expected_interval


def test_enrich_html_links_rejects_delay_with_rate_limit(tmp_path: Path) -> None:
    workflow_path = tmp_path / "invalid-detail-pacing.yaml"
    workflow_path.write_text(
        """
version: 1
workflow:
  name: invalid-detail-pacing
steps:
  - enrich_html_links:
      content: '<a href="/detail">Detail</a>'
      base_url: https://example.com
      link_selector: a
      detail_selectors: main
      delay: 1
      rate_limit: 2
      save_as: enriched_html
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="delay and rate_limit are mutually exclusive"):
        WorkflowRunner().load(workflow_path)
