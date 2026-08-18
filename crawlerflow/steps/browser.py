from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import BaseStep, step
from crawlerflow.events import EventName


class GotoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1)


@step("goto")
class GotoStep(BaseStep[GotoConfig]):
    config_model = GotoConfig

    async def execute(self, context: WorkflowContext) -> None:
        await context.require_browser().goto(self.config.url)


class SelectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector: str = Field(min_length=1)


@step("click")
class ClickStep(BaseStep[SelectorConfig]):
    config_model = SelectorConfig

    async def execute(self, context: WorkflowContext) -> None:
        await context.require_browser().click(self.config.selector)


class TypeConfig(SelectorConfig):
    value: str


@step("type")
class TypeStep(BaseStep[TypeConfig]):
    config_model = TypeConfig

    async def execute(self, context: WorkflowContext) -> None:
        await context.require_browser().fill(self.config.selector, self.config.value)


class SelectConfig(TypeConfig):
    pass


@step("select")
class SelectStep(BaseStep[SelectConfig]):
    config_model = SelectConfig

    async def execute(self, context: WorkflowContext) -> None:
        await context.require_browser().select(self.config.selector, self.config.value)


class WaitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector: str = Field(min_length=1)
    timeout: float | None = Field(default=None, gt=0)


@step("wait")
class WaitStep(BaseStep[WaitConfig]):
    config_model = WaitConfig

    async def execute(self, context: WorkflowContext) -> None:
        await context.require_browser().wait(self.config.selector, self.config.timeout)


class WaitNetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout: float | None = Field(default=None, gt=0)


@step("wait_network")
class WaitNetworkStep(BaseStep[WaitNetworkConfig]):
    config_model = WaitNetworkConfig

    async def execute(self, context: WorkflowContext) -> None:
        await context.require_browser().wait_network(self.config.timeout)


class EvaluateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: str = Field(min_length=1)
    save_as: str | None = None


@step("evaluate")
class EvaluateStep(BaseStep[EvaluateConfig]):
    config_model = EvaluateConfig

    async def execute(self, context: WorkflowContext) -> Any:
        result = await context.require_browser().evaluate(self.config.script)
        if self.config.save_as:
            context.outputs[self.config.save_as] = result
        return result


class ExtractConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector: str = Field(min_length=1)
    text: bool = False
    html: bool = False
    attribute: str | None = None
    save_as: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_mode(self) -> ExtractConfig:
        modes = int(self.text) + int(self.html) + int(self.attribute is not None)
        if modes != 1:
            raise ValueError("extract requires exactly one of text, html, or attribute")
        return self


@step("extract")
class ExtractStep(BaseStep[ExtractConfig]):
    config_model = ExtractConfig

    async def execute(self, context: WorkflowContext) -> Any:
        selector = json.dumps(self.config.selector)
        if self.config.text:
            expression = "element.textContent"
        elif self.config.html:
            expression = "element.innerHTML"
        else:
            expression = f"element.getAttribute({json.dumps(self.config.attribute)})"
        script = (
            f"const element = document.querySelector({selector}); "
            f"if (!element) throw new Error('Element not found: ' + {selector}); "
            f"return {expression};"
        )
        result = await context.require_browser().evaluate(script)
        context.outputs[self.config.save_as] = result
        return result


class BrowserRequestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = "GET"
    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None
    data: Any = None
    save_as: str | None = None


@step("browser_request")
class BrowserRequestStep(BaseStep[BrowserRequestConfig]):
    config_model = BrowserRequestConfig

    async def execute(self, context: WorkflowContext) -> Any:
        method = self.config.method.upper()
        started_at = time.monotonic()
        await context.emit_event(
            EventName.REQUEST_STARTED,
            {"transport": "browser", "method": method, "url": self.config.url},
        )
        try:
            response = await context.require_browser().request(
                method,
                self.config.url,
                headers=self.config.headers,
                data=self.config.data,
            )
        except Exception as error:
            await context.emit_event(
                EventName.REQUEST_FINISHED,
                {
                    "transport": "browser",
                    "method": method,
                    "url": self.config.url,
                    "succeeded": False,
                    "error": str(error),
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
            )
            raise
        await context.emit_event(
            EventName.REQUEST_FINISHED,
            {
                "transport": "browser",
                "method": method,
                "url": self.config.url,
                "succeeded": True,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            },
        )
        context.last_response = response
        result = {
            "status_code": response.status_code,
            "headers": response.headers,
            "body": response.body,
        }
        if self.config.save_as:
            context.outputs[self.config.save_as] = result
        return result


class _RequestThrottle:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._last_finished_at: float | None = None

    async def wait(self) -> None:
        if self._last_finished_at is None or self.interval <= 0:
            return
        remaining = self.interval - (time.monotonic() - self._last_finished_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

    def mark_finished(self) -> None:
        self._last_finished_at = time.monotonic()


class EnrichHtmlLinksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    base_url: str = Field(min_length=1)
    link_selector: str = Field(min_length=1)
    detail_selectors: list[str] = Field(min_length=1)
    detail_wait_selector: str | None = Field(default=None, min_length=1)
    timeout: float = Field(default=30, gt=0)
    delay: float = Field(default=0, ge=0)
    rate_limit: float | None = Field(default=None, gt=0)
    wrapper_tag: str = Field(default="div", pattern=r"^[A-Za-z][A-Za-z0-9-]*$")
    wrapper_class: str = "crawlerflow-linked-content"
    headers: dict[str, str] | None = None
    on_link_error: Literal["fail", "continue"] = "fail"
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")

    @field_validator("detail_selectors", mode="before")
    @classmethod
    def normalize_detail_selectors(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("detail_selectors")
    @classmethod
    def validate_detail_selectors(cls, selectors: list[str]) -> list[str]:
        if any(not selector.strip() for selector in selectors):
            raise ValueError("enrich_html_links detail selectors cannot be empty")
        return selectors

    @model_validator(mode="after")
    def validate_request_pacing(self) -> EnrichHtmlLinksConfig:
        if self.delay > 0 and self.rate_limit is not None:
            raise ValueError("enrich_html_links delay and rate_limit are mutually exclusive")
        return self

    @property
    def request_interval(self) -> float:
        if self.rate_limit is not None:
            return 1 / self.rate_limit
        return self.delay


@step("enrich_html_links")
class EnrichHtmlLinksStep(BaseStep[EnrichHtmlLinksConfig]):
    config_model = EnrichHtmlLinksConfig

    async def execute(self, context: WorkflowContext) -> str:
        browser = context.require_browser()
        links = await browser.evaluate(
            self._links_script(
                self.config.content,
                self.config.base_url,
                self.config.link_selector,
            )
        )
        if not isinstance(links, list):
            raise ValueError("enrich_html_links link evaluation must return a list")

        cache: dict[str, str | None] = {}
        insertions: list[dict[str, Any]] = []
        throttle = _RequestThrottle(self.config.request_interval)
        for link in links:
            index, url = self._validate_link(link)
            if url not in cache:
                try:
                    cache[url] = await self._load_detail(context, url, throttle)
                except Exception as error:
                    if self.config.on_link_error == "fail":
                        raise
                    cache[url] = None
                    context.storage.setdefault("link_enrichment_errors", []).append(
                        {"url": url, "error": str(error)}
                    )
            detail_html = cache[url]
            if detail_html:
                insertions.append({"index": index, "url": url, "html": detail_html})

        enriched_html = self.config.content
        if insertions:
            enriched_html = await browser.evaluate(
                self._injection_script(
                    self.config.content,
                    self.config.link_selector,
                    insertions,
                    self.config.wrapper_tag,
                    self.config.wrapper_class,
                )
            )
            if not isinstance(enriched_html, str):
                raise ValueError("enrich_html_links injection must return HTML text")

        context.outputs[self.config.save_as] = enriched_html
        context.last_html = enriched_html
        return enriched_html

    async def _load_detail(
        self,
        context: WorkflowContext,
        url: str,
        throttle: _RequestThrottle,
    ) -> str:
        browser = context.require_browser()
        started_at = time.monotonic()
        await context.emit_event(
            EventName.REQUEST_STARTED,
            {"transport": "browser", "method": "GET", "url": url},
        )
        try:
            deadline = time.monotonic() + self.config.timeout
            while True:
                await throttle.wait()
                try:
                    response = await browser.request("GET", url, headers=self.config.headers)
                finally:
                    throttle.mark_finished()
                if not 200 <= response.status_code < 400:
                    raise RuntimeError(
                        f"Detail request returned HTTP {response.status_code}: {url}"
                    )
                source = self._response_text(response.body)
                detail_result = await browser.evaluate(
                    self._detail_script(
                        source,
                        self.config.detail_selectors,
                        self.config.detail_wait_selector,
                    )
                )
                ready, detail_html = self._validate_detail_result(detail_result)
                if ready:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Detail wait selector was not found within "
                        f"{self.config.timeout:g} seconds "
                        f"({self.config.detail_wait_selector}): {url}"
                    )
                await asyncio.sleep(min(0.25, remaining))
        except Exception as error:
            await context.emit_event(
                EventName.REQUEST_FINISHED,
                {
                    "transport": "browser",
                    "method": "GET",
                    "url": url,
                    "succeeded": False,
                    "error": str(error),
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
            )
            raise
        context.last_response = response
        await context.emit_event(
            EventName.REQUEST_FINISHED,
            {
                "transport": "browser",
                "method": "GET",
                "url": url,
                "succeeded": True,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            },
        )
        return detail_html

    @staticmethod
    def _validate_link(link: Any) -> tuple[int, str]:
        if not isinstance(link, dict):
            raise ValueError("enrich_html_links links must be mappings")
        index = link.get("index")
        url = link.get("url")
        if not isinstance(index, int) or not isinstance(url, str) or not url:
            raise ValueError("enrich_html_links received an invalid link")
        return index, url

    @staticmethod
    def _validate_detail_result(result: Any) -> tuple[bool, str]:
        if not isinstance(result, dict):
            raise ValueError("enrich_html_links detail evaluation must return a mapping")
        ready = result.get("ready")
        detail_html = result.get("html")
        if not isinstance(ready, bool) or not isinstance(detail_html, str):
            raise ValueError("enrich_html_links received an invalid detail result")
        return ready, detail_html

    @staticmethod
    def _response_text(body: str | bytes | None) -> str:
        if body is None:
            return ""
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return body

    @staticmethod
    def _links_script(content: str, base_url: str, selector: str) -> str:
        return (
            "(() => { "
            f"const linkSource = {json.dumps(content)}; "
            f"const baseUrl = {json.dumps(base_url)}; "
            f"const linkSelector = {json.dumps(selector)}; "
            "const parsed = new DOMParser().parseFromString(linkSource, 'text/html'); "
            "return Array.from(parsed.querySelectorAll(linkSelector)).flatMap((anchor, index) => { "
            "const href = anchor.getAttribute('href'); "
            "if (!href) return []; "
            "const url = new URL(href, baseUrl); "
            "if (!['http:', 'https:'].includes(url.protocol)) return []; "
            "return [{ index, url: url.href }]; "
            "}); "
            "})()"
        )

    @staticmethod
    def _detail_script(
        content: str,
        selectors: list[str],
        wait_selector: str | None,
    ) -> str:
        return (
            "(() => { "
            f"const detailSource = {json.dumps(content)}; "
            f"const selectors = {json.dumps(selectors)}; "
            f"const waitSelector = {json.dumps(wait_selector)}; "
            "const parsed = new DOMParser().parseFromString(detailSource, 'text/html'); "
            "const selectAll = (selector) => { "
            "if (selector.startsWith('/') || selector.startsWith('./')) { "
            "const result = parsed.evaluate(selector, parsed, null, "
            "XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); "
            "return Array.from({ length: result.snapshotLength }, "
            "(_, index) => result.snapshotItem(index)); "
            "} "
            "return Array.from(parsed.querySelectorAll(selector)); "
            "}; "
            "const ready = !waitSelector || selectAll(waitSelector).length > 0; "
            "const elements = []; "
            "const seen = new Set(); "
            "for (const selector of selectors) { "
            "for (const element of selectAll(selector)) { "
            "if (element && !seen.has(element)) { seen.add(element); elements.push(element); } "
            "} "
            "} "
            "return { ready, html: elements.map((element) => element.outerHTML).join('\\n') }; "
            "})()"
        )

    @staticmethod
    def _injection_script(
        content: str,
        link_selector: str,
        insertions: list[dict[str, Any]],
        wrapper_tag: str,
        wrapper_class: str,
    ) -> str:
        return (
            "(() => { "
            f"const parentSource = {json.dumps(content)}; "
            f"const linkSelector = {json.dumps(link_selector)}; "
            f"const insertions = {json.dumps(insertions)}; "
            f"const wrapperTag = {json.dumps(wrapper_tag)}; "
            f"const wrapperClass = {json.dumps(wrapper_class)}; "
            "const parsed = new DOMParser().parseFromString(parentSource, 'text/html'); "
            "const anchors = Array.from(parsed.querySelectorAll(linkSelector)); "
            "for (const insertion of insertions) { "
            "const anchor = anchors[insertion.index]; "
            "if (!anchor) continue; "
            "const wrapper = parsed.createElement(wrapperTag); "
            "if (wrapperClass) wrapper.className = wrapperClass; "
            "wrapper.setAttribute('data-crawlerflow-source', insertion.url); "
            "wrapper.innerHTML = insertion.html; "
            "anchor.insertAdjacentElement('afterend', wrapper); "
            "} "
            "const fullDocument = /^\\s*(<!doctype|<html[\\s>])/i.test(parentSource); "
            "const doctype = parsed.doctype ? `<!DOCTYPE ${parsed.doctype.name}>\\n` : ''; "
            "return fullDocument ? doctype + parsed.documentElement.outerHTML "
            ": parsed.body.innerHTML; "
            "})()"
        )


class DownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1)
    path: str = Field(min_length=1)
    save_as: str | None = Field(default=None, pattern=r"^[A-Za-z_]\w*$")


@step("download")
class DownloadStep(BaseStep[DownloadConfig]):
    config_model = DownloadConfig

    async def execute(self, context: WorkflowContext) -> str:
        path = context.resolve_path(self.config.path)
        result = await context.require_browser().download(self.config.url, path)
        value = str(result)
        if self.config.save_as:
            context.outputs[self.config.save_as] = value
        return value


class ScreenshotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    save_as: str | None = Field(default=None, pattern=r"^[A-Za-z_]\w*$")


@step("screenshot")
class ScreenshotStep(BaseStep[ScreenshotConfig]):
    config_model = ScreenshotConfig

    async def execute(self, context: WorkflowContext) -> str:
        path = context.resolve_path(self.config.path)
        result = await context.require_browser().screenshot(path)
        value = str(result)
        if self.config.save_as:
            context.outputs[self.config.save_as] = value
        return value


class GetCookiesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    save_as: str = Field(default="cookies", pattern=r"^[A-Za-z_]\w*$")


@step("get_cookies")
class GetCookiesStep(BaseStep[GetCookiesConfig]):
    config_model = GetCookiesConfig

    async def execute(self, context: WorkflowContext) -> dict[str, str]:
        cookies = await context.require_browser().cookies()
        context.cookies = dict(cookies)
        context.outputs[self.config.save_as] = dict(cookies)
        return cookies


class SetCookiesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookies: dict[str, str]


@step("set_cookies")
class SetCookiesStep(BaseStep[SetCookiesConfig]):
    config_model = SetCookiesConfig

    async def execute(self, context: WorkflowContext) -> None:
        cookies = dict(self.config.cookies)
        await context.require_browser().set_cookies(cookies)
        context.cookies.update(cookies)
