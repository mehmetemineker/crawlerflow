from __future__ import annotations

import asyncio
import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from rich.console import Console

from crawlerflow.browser import BrowserResponse
from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import BaseStep, step
from crawlerflow.events import EventName

console = Console()


class LogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


@step("log")
class LogStep(BaseStep[LogConfig]):
    config_model = LogConfig

    async def execute(self, context: WorkflowContext) -> None:
        console.print(self.config.message)


class SleepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(ge=0)


@step("sleep")
class SleepStep(BaseStep[SleepConfig]):
    config_model = SleepConfig

    async def execute(self, context: WorkflowContext) -> None:
        await asyncio.sleep(self.config.seconds)


class SaveTextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    content: str | int | float | bool
    encoding: str = "utf-8"


@step("save_text")
class SaveTextStep(BaseStep[SaveTextConfig]):
    config_model = SaveTextConfig

    async def execute(self, context: WorkflowContext) -> None:
        path = context.resolve_path(self.config.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(self.config.content), encoding=self.config.encoding)


class SaveJsonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    data: Any
    indent: int | None = Field(default=2, ge=0)


@step("save_json")
class SaveJsonStep(BaseStep[SaveJsonConfig]):
    config_model = SaveJsonConfig

    async def execute(self, context: WorkflowContext) -> None:
        path = context.resolve_path(self.config.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.config.data, ensure_ascii=False, indent=self.config.indent),
            encoding="utf-8",
        )


class ExtractJavascriptArrayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    variable: str = Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*$")
    declaration_kind: Literal["const", "let", "var"] | None = "const"
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")


@step("extract_javascript_array")
class ExtractJavascriptArrayStep(BaseStep[ExtractJavascriptArrayConfig]):
    config_model = ExtractJavascriptArrayConfig

    async def execute(self, context: WorkflowContext) -> str:
        array_source = self._extract_array(self.config.content, self.config.variable)
        if self.config.declaration_kind is None:
            javascript = array_source
        else:
            javascript = (
                f"{self.config.declaration_kind} {self.config.variable} = "
                f"{array_source};\n"
            )
        context.outputs[self.config.save_as] = javascript
        return javascript

    @classmethod
    def _extract_array(cls, content: str, variable: str) -> str:
        escaped_variable = re.escape(variable)
        assignment_pattern = re.compile(
            rf"(?<![A-Za-z0-9_$])"
            rf"(?:(?:var|let|const)\s+|(?:window|globalThis)\s*\.\s*)?"
            rf"{escaped_variable}(?![A-Za-z0-9_$])\s*=",
        )
        for assignment in assignment_pattern.finditer(content):
            start = assignment.end()
            while start < len(content) and content[start].isspace():
                start += 1
            if start < len(content) and content[start] == "[":
                end = cls._find_array_end(content, start)
                if end is not None:
                    return content[start : end + 1]
        raise ValueError(f"JavaScript array variable was not found: {variable}")

    @staticmethod
    def _find_array_end(content: str, start: int) -> int | None:
        depth = 0
        quote: str | None = None
        escaped = False
        line_comment = False
        block_comment = False
        index = start
        while index < len(content):
            character = content[index]
            following = content[index + 1] if index + 1 < len(content) else ""
            if line_comment:
                if character in "\r\n":
                    line_comment = False
            elif block_comment:
                if character == "*" and following == "/":
                    block_comment = False
                    index += 1
            elif quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character == "/" and following == "/":
                line_comment = True
                index += 1
            elif character == "/" and following == "*":
                block_comment = True
                index += 1
            elif character in {'"', "'", "`"}:
                quote = character
            elif character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    return index
            index += 1
        return None


class SaveHtmlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    content: str | None = None
    selectors: list[str] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, gt=0)
    ignore_comments: bool = False
    skip_if_empty: bool = False
    pretty: bool = False

    @field_validator("selectors", mode="before")
    @classmethod
    def normalize_selectors(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("selectors")
    @classmethod
    def validate_selectors(cls, selectors: list[str]) -> list[str]:
        if any(not selector.strip() for selector in selectors):
            raise ValueError("save_html selectors cannot be empty")
        return selectors


@dataclass(slots=True)
class _HtmlFrame:
    tag: str
    line_index: int
    has_content: bool = False
    inline: bool = False
    raw: bool = False
    pending_space: bool = False


class _HtmlPrettyPrinter(HTMLParser):
    _void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    _raw_tags = {"pre", "script", "style", "textarea"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.lines: list[str] = []
        self.depth = 0
        self.stack: list[_HtmlFrame] = []

    def handle_starttag(
        self,
        tag: str,
        _attrs: list[tuple[str, str | None]],
    ) -> None:
        self._mark_parent_content()
        opening, output_tag = self._normalize_start_tag(self.get_starttag_text())
        self.lines.append(f"{'  ' * self.depth}{opening}")
        if tag in self._void_tags:
            return
        self.stack.append(
            _HtmlFrame(
                tag=output_tag,
                line_index=len(self.lines) - 1,
                raw=tag in self._raw_tags,
            )
        )
        self.depth += 1

    def handle_startendtag(
        self,
        _tag: str,
        _attrs: list[tuple[str, str | None]],
    ) -> None:
        self._mark_parent_content()
        opening, _ = self._normalize_start_tag(self.get_starttag_text())
        self.lines.append(f"{'  ' * self.depth}{opening}")

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            self.lines.append(f"{'  ' * self.depth}</{tag}>")
            return
        frame = self.stack.pop()
        self.depth = max(0, self.depth - 1)
        closing = f"</{frame.tag}>"
        if frame.line_index == len(self.lines) - 1:
            self.lines[-1] += closing
        else:
            self.lines.append(f"{'  ' * self.depth}{closing}")

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        raw = bool(self.stack and self.stack[-1].raw)
        value = data if raw else escape(" ".join(data.split()), quote=False)
        self._append_text(
            value,
            leading_space=not raw and data[0].isspace(),
            trailing_space=not raw and data[-1].isspace(),
        )

    def handle_entityref(self, name: str) -> None:
        self._append_text(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._append_text(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._mark_parent_content()
        self.lines.append(f"{'  ' * self.depth}<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.lines.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.lines.append(f"{'  ' * self.depth}<?{data}>")

    def formatted(self) -> str:
        return "\n".join(self.lines)

    def _append_text(
        self,
        value: str,
        *,
        leading_space: bool = False,
        trailing_space: bool = False,
    ) -> None:
        if not value:
            return
        if self.stack:
            frame = self.stack[-1]
            if frame.line_index == len(self.lines) - 1 and (
                not frame.has_content or frame.inline
            ):
                if frame.inline and (leading_space or frame.pending_space):
                    value = f" {value}"
                self.lines[-1] += value
                frame.has_content = True
                frame.inline = True
                frame.pending_space = trailing_space
                return
            frame.has_content = True
            frame.pending_space = trailing_space
        self.lines.append(f"{'  ' * self.depth}{value}")

    def _mark_parent_content(self) -> None:
        if self.stack:
            self.stack[-1].has_content = True

    @staticmethod
    def _normalize_start_tag(source: str) -> tuple[str, str]:
        source = source.strip()
        self_closing = source.endswith("/>")
        body = source[1 : -2 if self_closing else -1].strip()
        index = 0
        while index < len(body) and not body[index].isspace():
            index += 1
        tag = body[:index]
        attributes: list[str] = []
        while index < len(body):
            while index < len(body) and body[index].isspace():
                index += 1
            if index >= len(body):
                break
            start = index
            while index < len(body) and not body[index].isspace() and body[index] != "=":
                index += 1
            while index < len(body) and body[index].isspace():
                index += 1
            if index < len(body) and body[index] == "=":
                index += 1
                while index < len(body) and body[index].isspace():
                    index += 1
                if index < len(body) and body[index] in {'"', "'"}:
                    quote = body[index]
                    index += 1
                    while index < len(body) and body[index] != quote:
                        index += 1
                    if index < len(body):
                        index += 1
                else:
                    while index < len(body) and not body[index].isspace():
                        index += 1
            attributes.append(body[start:index].strip())
        suffix = " />" if self_closing else ">"
        opening = f"<{tag}{''.join(f' {attribute}' for attribute in attributes)}{suffix}"
        return opening, tag


@dataclass(slots=True, frozen=True)
class _SimpleHtmlSelector:
    tag: str | None
    element_id: str | None
    classes: frozenset[str]

    _pattern = re.compile(
        r"(?P<tag>[A-Za-z][A-Za-z0-9:_-]*)?"
        r"(?P<suffix>(?:[.#][A-Za-z_][A-Za-z0-9_-]*)*)"
    )

    @classmethod
    def parse(cls, selector: str) -> _SimpleHtmlSelector:
        match = cls._pattern.fullmatch(selector.strip())
        if match is None or not (match.group("tag") or match.group("suffix")):
            raise ValueError(
                "Browser-free save_html selectors support tag, #id, and .class combinations"
            )
        element_id: str | None = None
        classes: set[str] = set()
        for prefix, value in re.findall(
            r"([.#])([A-Za-z_][A-Za-z0-9_-]*)",
            match.group("suffix"),
        ):
            if prefix == "#":
                if element_id is not None:
                    raise ValueError("Browser-free save_html selector cannot contain multiple IDs")
                element_id = value
            else:
                classes.add(value)
        return cls(
            tag=match.group("tag").lower() if match.group("tag") else None,
            element_id=element_id,
            classes=frozenset(classes),
        )

    def matches(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        attributes = {name: value or "" for name, value in attrs}
        if self.tag is not None and self.tag != tag:
            return False
        if self.element_id is not None and attributes.get("id") != self.element_id:
            return False
        element_classes = frozenset(attributes.get("class", "").split())
        return self.classes <= element_classes


@dataclass(slots=True, frozen=True)
class _HtmlSelector:
    parts: tuple[_SimpleHtmlSelector, ...]

    @classmethod
    def parse(cls, selector: str) -> _HtmlSelector:
        parts = tuple(
            _SimpleHtmlSelector.parse(part)
            for part in re.split(r"\s+", selector.strip())
            if part
        )
        if not parts:
            raise ValueError(
                "Browser-free save_html selectors support tag, #id, .class, and descendant "
                "combinations"
            )
        return cls(parts=parts)

    def matches(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        ancestors: list[tuple[str, list[tuple[str, str | None]]]],
    ) -> bool:
        if not self.parts[-1].matches(tag, attrs):
            return False
        ancestor_index = len(ancestors) - 1
        for part in reversed(self.parts[:-1]):
            while ancestor_index >= 0:
                ancestor_tag, ancestor_attrs = ancestors[ancestor_index]
                ancestor_index -= 1
                if part.matches(ancestor_tag, ancestor_attrs):
                    break
            else:
                return False
        return True


@dataclass(slots=True)
class _HtmlCapture:
    selector_index: int
    index: int
    parts: list[str]
    depth: int


class _HtmlFragmentSelector(HTMLParser):
    _void_tags = _HtmlPrettyPrinter._void_tags

    def __init__(self, selectors: list[str]) -> None:
        super().__init__(convert_charrefs=False)
        self.selectors = [_HtmlSelector.parse(selector) for selector in selectors]
        self.ancestors: list[tuple[str, list[tuple[str, str | None]]]] = []
        self.active: list[_HtmlCapture] = []
        self.results: list[tuple[int, int, str]] = []
        self._match_index = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        source = self.get_starttag_text()
        is_void = tag in self._void_tags
        for capture in self.active:
            capture.parts.append(source)
            if not is_void:
                capture.depth += 1
        selector_index = self._matching_selector_index(tag, attrs)
        if selector_index is not None:
            if is_void:
                self.results.append((selector_index, self._match_index, source))
            else:
                self.active.append(
                    _HtmlCapture(selector_index, self._match_index, [source], 1)
                )
            self._match_index += 1
        if not is_void:
            self.ancestors.append((tag, attrs))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        source = self.get_starttag_text()
        self._append_to_active(source)
        selector_index = self._matching_selector_index(tag, attrs)
        if selector_index is not None:
            self.results.append((selector_index, self._match_index, source))
            self._match_index += 1

    def handle_endtag(self, tag: str) -> None:
        source = f"</{tag}>"
        completed: list[_HtmlCapture] = []
        for capture in self.active:
            capture.parts.append(source)
            capture.depth -= 1
            if capture.depth == 0:
                completed.append(capture)
        for capture in completed:
            self.active.remove(capture)
            self.results.append(
                (capture.selector_index, capture.index, "".join(capture.parts))
            )
        for index in range(len(self.ancestors) - 1, -1, -1):
            if self.ancestors[index][0] == tag:
                del self.ancestors[index:]
                break

    def handle_data(self, data: str) -> None:
        self._append_to_active(data)

    def handle_entityref(self, name: str) -> None:
        self._append_to_active(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._append_to_active(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._append_to_active(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._append_to_active(f"<!{decl}>")

    def selected_html(self) -> str:
        return "\n".join(html for _, _, html in sorted(self.results))

    def _matching_selector_index(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> int | None:
        for index, selector in enumerate(self.selectors):
            if selector.matches(tag, attrs, self.ancestors):
                return index
        return None

    def _append_to_active(self, value: str) -> None:
        for capture in self.active:
            capture.parts.append(value)


@step("save_html")
class SaveHtmlStep(BaseStep[SaveHtmlConfig]):
    config_model = SaveHtmlConfig

    async def execute(self, context: WorkflowContext) -> None:
        html = self.config.content
        if html is None:
            html = await context.require_browser().html()
        if self.config.selectors:
            if context.browser is None:
                html = self._select_without_browser(
                    html,
                    self.config.selectors,
                    self.config.limit,
                    self.config.offset,
                )
            else:
                html = await context.browser.evaluate(
                    self._selection_script(
                        html,
                        self.config.selectors,
                        self.config.limit,
                        self.config.offset,
                    )
                )
                if not isinstance(html, str):
                    raise ValueError("save_html selector evaluation must return HTML text")
        if self.config.ignore_comments:
            html = self._remove_comments(html)
        if self.config.skip_if_empty and not html.strip():
            context.last_html = html
            return
        if self.config.pretty:
            if context.browser is None:
                html = self._format_without_browser(html)
            else:
                html = await context.browser.evaluate(self._format_script(html))
                if not isinstance(html, str):
                    raise ValueError("save_html formatting must return HTML text")
        context.last_html = html
        path = context.resolve_path(self.config.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    @staticmethod
    def _selection_script(
        html: str,
        selectors: list[str],
        limit: int | None = None,
        offset: int = 0,
    ) -> str:
        encoded_html = json.dumps(html)
        encoded_selectors = json.dumps(selectors)
        encoded_limit = json.dumps(limit)
        encoded_offset = json.dumps(offset)
        return (
            "(() => { "
            f"const source = {encoded_html}; "
            f"const selectors = {encoded_selectors}; "
            f"const limit = {encoded_limit}; "
            f"const offset = {encoded_offset}; "
            "const parsed = new DOMParser().parseFromString(source, 'text/html'); "
            "const elements = []; "
            "const seen = new Set(); "
            "for (const selector of selectors) { "
            "let matches; "
            "if (selector.startsWith('/') || selector.startsWith('./')) { "
            "const result = parsed.evaluate(selector, parsed, null, "
            "XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); "
            "matches = Array.from({ length: result.snapshotLength }, "
            "(_, index) => result.snapshotItem(index)); "
            "} else { matches = Array.from(parsed.querySelectorAll(selector)); } "
            "for (const element of matches) { "
            "if (element && !seen.has(element)) { seen.add(element); elements.push(element); } "
            "} "
            "} "
            "const selected = limit === null "
            "? elements.slice(offset) : elements.slice(offset, offset + limit); "
            "return selected.map((element) => element.outerHTML).join('\\n'); "
            "})()"
        )

    @staticmethod
    def _format_script(html: str) -> str:
        encoded_html = json.dumps(html)
        return (
            "(() => { "
            f"const formatSource = {encoded_html}; "
            "const parsed = new DOMParser().parseFromString(formatSource, 'text/html'); "
            "const lines = []; "
            "const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', "
            "'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']); "
            "const rawTags = new Set(['pre', 'script', 'style', 'textarea']); "
            "const escapeText = (value) => value.replaceAll('&', '&amp;')"
            ".replaceAll('<', '&lt;').replaceAll('>', '&gt;'); "
            "const escapeAttribute = (value) => escapeText(value).replaceAll('\"', '&quot;'); "
            "const render = (node, depth) => { "
            "const indent = '  '.repeat(depth); "
            "if (node.nodeType === 3) { "
            "const text = node.textContent.replace(/\\s+/g, ' ').trim(); "
            "if (text) lines.push(indent + escapeText(text)); "
            "return; "
            "} "
            "if (node.nodeType === 8) { lines.push(`${indent}<!--${node.data}-->`); return; } "
            "if (node.nodeType !== 1) return; "
            "const tag = node.tagName.toLowerCase(); "
            "const attributes = Array.from(node.attributes).map((attribute) => "
            "`${attribute.name}=\"${escapeAttribute(attribute.value)}\"`).join(' '); "
            "const opening = `<${tag}${attributes ? ` ${attributes}` : ''}>`; "
            "if (voidTags.has(tag)) { lines.push(indent + opening); return; } "
            "if (rawTags.has(tag)) { "
            "lines.push(`${indent}${opening}${node.innerHTML}</${tag}>`); return; "
            "} "
            "const children = Array.from(node.childNodes).filter((child) => "
            "child.nodeType !== 3 || child.textContent.trim()); "
            "if (children.length === 0) { lines.push(`${indent}${opening}</${tag}>`); return; } "
            "if (children.length === 1 && children[0].nodeType === 3) { "
            "const text = children[0].textContent.replace(/\\s+/g, ' ').trim(); "
            "lines.push(`${indent}${opening}${escapeText(text)}</${tag}>`); return; "
            "} "
            "lines.push(indent + opening); "
            "for (const child of children) render(child, depth + 1); "
            "lines.push(`${indent}</${tag}>`); "
            "}; "
            "const fullDocument = /^\\s*(<!doctype|<html[\\s>])/i.test(formatSource); "
            "if (fullDocument) { "
            "if (parsed.doctype) lines.push(`<!DOCTYPE ${parsed.doctype.name}>`); "
            "render(parsed.documentElement, 0); "
            "} else { "
            "for (const child of Array.from(parsed.body.childNodes)) render(child, 0); "
            "} "
            "return lines.join('\\n'); "
            "})()"
        )

    @staticmethod
    def _format_without_browser(html: str) -> str:
        formatter = _HtmlPrettyPrinter()
        formatter.feed(html)
        formatter.close()
        return formatter.formatted()

    @staticmethod
    def _remove_comments(html: str) -> str:
        return re.sub(r"<!--[\s\S]*?-->", "", html)

    @staticmethod
    def _select_without_browser(
        html: str,
        selectors: list[str],
        limit: int | None = None,
        offset: int = 0,
    ) -> str:
        selector = _HtmlFragmentSelector(selectors)
        selector.feed(html)
        selector.close()
        selected = sorted(selector.results)[offset:]
        if limit is not None:
            selected = selected[:limit]
        return "\n".join(fragment for _, _, fragment in selected)


class EnrichHtmlLinksHttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    base_url: str = Field(min_length=1)
    parent_selector: str = Field(min_length=1)
    href_contains: str = Field(min_length=1)
    detail_regex: str = Field(min_length=1)
    detail_template: str = Field(min_length=1)
    wrapper_tag: str = Field(default="div", pattern=r"^[A-Za-z][A-Za-z0-9-]*$")
    wrapper_class: str = "crawlerflow-linked-content"
    headers: dict[str, str] | None = None
    timeout: float = Field(default=30, gt=0)
    delay: float = Field(default=0, ge=0)
    on_link_error: Literal["fail", "continue"] = "fail"
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")

    @field_validator("detail_regex")
    @classmethod
    def validate_detail_regex(cls, pattern: str) -> str:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"Invalid enrich_html_links_http detail_regex: {error}") from error
        return pattern


@dataclass(slots=True)
class _HtmlLinkFrame:
    tag: str
    attrs: list[tuple[str, str | None]]
    target_index: int | None
    active_target_index: int | None


class _HtmlLinkCollector(HTMLParser):
    _void_tags = _HtmlPrettyPrinter._void_tags

    def __init__(self, parent_selector: str, base_url: str, href_contains: str) -> None:
        super().__init__(convert_charrefs=False)
        self.selector = _HtmlSelector.parse(parent_selector)
        self.base_url = base_url
        self.href_contains = href_contains
        self.frames: list[_HtmlLinkFrame] = []
        self.links: list[tuple[int, str]] = []
        self._seen: set[tuple[int, str]] = set()
        self._target_index = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._handle_element(tag, attrs, tag in self._void_tags)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._handle_element(tag, attrs, True)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.frames) - 1, -1, -1):
            if self.frames[index].tag == tag:
                del self.frames[index:]
                return

    def _handle_element(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        self_closing: bool,
    ) -> None:
        ancestors = [(frame.tag, frame.attrs) for frame in self.frames]
        target_index = None
        if self.selector.matches(tag, attrs, ancestors):
            target_index = self._target_index
            self._target_index += 1
        active_target_index = target_index
        if active_target_index is None and self.frames:
            active_target_index = self.frames[-1].active_target_index
        if tag == "a" and active_target_index is not None:
            href = dict(attrs).get("href")
            if href and self.href_contains in href:
                url = urljoin(self.base_url, href)
                if urlparse(url).scheme in {"http", "https"}:
                    identity = (active_target_index, url)
                    if identity not in self._seen:
                        self._seen.add(identity)
                        self.links.append(identity)
        if not self_closing:
            self.frames.append(
                _HtmlLinkFrame(tag, attrs, target_index, active_target_index)
            )


class _HtmlLinkInjector(HTMLParser):
    _void_tags = _HtmlPrettyPrinter._void_tags

    def __init__(
        self,
        parent_selector: str,
        insertions: dict[int, list[tuple[str, str]]],
        wrapper_tag: str,
        wrapper_class: str,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.selector = _HtmlSelector.parse(parent_selector)
        self.insertions = insertions
        self.wrapper_tag = wrapper_tag
        self.wrapper_class = wrapper_class
        self.frames: list[_HtmlLinkFrame] = []
        self.parts: list[str] = []
        self._target_index = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.parts.append(self.get_starttag_text())
        self._push_element(tag, attrs, tag in self._void_tags)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.parts.append(self.get_starttag_text())
        self._push_element(tag, attrs, True)

    def handle_endtag(self, tag: str) -> None:
        matching_index = None
        for index in range(len(self.frames) - 1, -1, -1):
            if self.frames[index].tag == tag:
                matching_index = index
                break
        if matching_index is not None:
            frame = self.frames[matching_index]
            if frame.target_index is not None:
                self.parts.extend(self._wrapper_html(frame.target_index))
            del self.frames[matching_index:]
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def enriched_html(self) -> str:
        return "".join(self.parts)

    def _push_element(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        self_closing: bool,
    ) -> None:
        ancestors = [(frame.tag, frame.attrs) for frame in self.frames]
        target_index = None
        if self.selector.matches(tag, attrs, ancestors):
            target_index = self._target_index
            self._target_index += 1
        active_target_index = target_index
        if active_target_index is None and self.frames:
            active_target_index = self.frames[-1].active_target_index
        if not self_closing:
            self.frames.append(
                _HtmlLinkFrame(tag, attrs, target_index, active_target_index)
            )

    def _wrapper_html(self, target_index: int) -> list[str]:
        wrappers = []
        class_attribute = (
            f' class="{escape(self.wrapper_class, quote=True)}"'
            if self.wrapper_class
            else ""
        )
        for url, html in self.insertions.get(target_index, []):
            wrappers.append(
                f"<{self.wrapper_tag}{class_attribute} "
                f'data-crawlerflow-source="{escape(url, quote=True)}">'
                f"{html}</{self.wrapper_tag}>"
            )
        return wrappers


@step("enrich_html_links_http")
class EnrichHtmlLinksHttpStep(BaseStep[EnrichHtmlLinksHttpConfig]):
    config_model = EnrichHtmlLinksHttpConfig

    async def execute(self, context: WorkflowContext) -> str:
        collector = _HtmlLinkCollector(
            self.config.parent_selector,
            self.config.base_url,
            self.config.href_contains,
        )
        collector.feed(self.config.content)
        collector.close()

        cache: dict[str, str | None] = {}
        insertions: dict[int, list[tuple[str, str]]] = {}
        last_finished_at: float | None = None
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            for target_index, url in collector.links:
                if url not in cache:
                    if last_finished_at is not None and self.config.delay > 0:
                        remaining = self.config.delay - (time.monotonic() - last_finished_at)
                        if remaining > 0:
                            await asyncio.sleep(remaining)
                    try:
                        cache[url] = await self._load_detail(context, client, url)
                    except Exception as error:
                        if self.config.on_link_error == "fail":
                            raise
                        cache[url] = None
                        context.storage.setdefault("link_enrichment_errors", []).append(
                            {"url": url, "error": str(error)}
                        )
                    finally:
                        last_finished_at = time.monotonic()
                detail_html = cache[url]
                if detail_html:
                    insertions.setdefault(target_index, []).append((url, detail_html))

        injector = _HtmlLinkInjector(
            self.config.parent_selector,
            insertions,
            self.config.wrapper_tag,
            self.config.wrapper_class,
        )
        injector.feed(self.config.content)
        injector.close()
        enriched_html = injector.enriched_html()
        context.outputs[self.config.save_as] = enriched_html
        context.last_html = enriched_html
        return enriched_html

    async def _load_detail(
        self,
        context: WorkflowContext,
        client: httpx.AsyncClient,
        url: str,
    ) -> str:
        started_at = time.monotonic()
        await context.emit_event(
            EventName.REQUEST_STARTED,
            {"transport": "http", "method": "GET", "url": url},
        )
        try:
            response = await client.request("GET", url, headers=self.config.headers)
            if not 200 <= response.status_code < 400:
                raise RuntimeError(
                    f"Detail request returned HTTP {response.status_code}: {url}"
                )
            detail_html = self._render_detail(response.text)
        except Exception as error:
            await context.emit_event(
                EventName.REQUEST_FINISHED,
                {
                    "transport": "http",
                    "method": "GET",
                    "url": url,
                    "succeeded": False,
                    "error": str(error),
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
            )
            raise
        context.last_response = BrowserResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.text,
        )
        await context.emit_event(
            EventName.REQUEST_FINISHED,
            {
                "transport": "http",
                "method": "GET",
                "url": url,
                "succeeded": True,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            },
        )
        return detail_html

    def _render_detail(self, source: str) -> str:
        match = re.search(self.config.detail_regex, source, flags=re.DOTALL)
        if match is None:
            raise ValueError("enrich_html_links_http detail_regex did not match detail content")
        positional = [escape(value or "") for value in (match.group(0), *match.groups())]
        named = {name: escape(value or "") for name, value in match.groupdict().items()}
        try:
            return self.config.detail_template.format(*positional, **named)
        except (IndexError, KeyError, ValueError) as error:
            raise ValueError(
                f"Invalid enrich_html_links_http detail_template: {error}"
            ) from error


class ResolveLocationUrlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None
    timeout: float = Field(default=30, gt=0)
    max_redirects: int = Field(default=10, gt=0, le=50)
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("resolve_location_url url must be an HTTP(S) URL")
        return value


@step("resolve_location_url")
class ResolveLocationUrlStep(BaseStep[ResolveLocationUrlConfig]):
    config_model = ResolveLocationUrlConfig

    _coordinate = r"-?\d+(?:\.\d+)?"
    _data_pattern = re.compile(
        rf"!3d(?P<latitude>{_coordinate})!4d(?P<longitude>{_coordinate})",
        flags=re.IGNORECASE,
    )
    _at_pattern = re.compile(
        rf"/@(?P<latitude>{_coordinate}),(?P<longitude>{_coordinate})(?:,|/|$)",
        flags=re.IGNORECASE,
    )
    _pair_pattern = re.compile(
        rf"^\s*(?P<latitude>{_coordinate})\s*,\s*(?P<longitude>{_coordinate})\s*$"
    )
    _query_keys = ("query", "center", "destination", "origin", "ll", "q")

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        started_at = time.monotonic()
        request_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0 Safari/537.36"
            ),
            **(self.config.headers or {}),
        }
        await context.emit_event(
            EventName.REQUEST_STARTED,
            {"transport": "http", "method": "GET", "url": self.config.url},
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout,
                follow_redirects=True,
                max_redirects=self.config.max_redirects,
            ) as client:
                response = await client.request(
                    "GET",
                    self.config.url,
                    headers=request_headers,
                )
        except Exception as error:
            await context.emit_event(
                EventName.REQUEST_FINISHED,
                {
                    "transport": "http",
                    "method": "GET",
                    "url": self.config.url,
                    "succeeded": False,
                    "error": str(error),
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
            )
            raise

        final_url = str(response.url)
        await context.emit_event(
            EventName.REQUEST_FINISHED,
            {
                "transport": "http",
                "method": "GET",
                "url": self.config.url,
                "final_url": final_url,
                "succeeded": True,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            },
        )
        context.last_response = BrowserResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.text,
        )
        result = self._build_result(response, final_url)
        context.outputs[self.config.save_as] = result
        return result

    def _build_result(self, response: httpx.Response, final_url: str) -> dict[str, Any]:
        coordinates = self._extract_from_url(final_url)
        source = "final_url"
        if coordinates is None:
            coordinates = self._extract_from_text(response.text)
            source = "response_body"
        if coordinates is None:
            raise ValueError(
                "resolve_location_url could not find coordinates in the final URL "
                "or response body"
            )
        latitude, longitude = coordinates
        return {
            "original_url": self.config.url,
            "final_url": final_url,
            "status_code": response.status_code,
            "redirect_count": len(response.history),
            "latitude": latitude,
            "longitude": longitude,
            "source": source,
        }

    @classmethod
    def _extract_from_url(cls, url: str) -> tuple[float, float] | None:
        normalized = cls._decode_repeatedly(url)
        parsed = urlparse(normalized)
        query = parse_qs(parsed.query)
        for key in cls._query_keys:
            for value in query.get(key, []):
                coordinates = cls._extract_pair(value)
                if coordinates is not None:
                    return coordinates
        return cls._extract_from_text(normalized)

    @classmethod
    def _extract_from_text(cls, source: str) -> tuple[float, float] | None:
        normalized = cls._decode_repeatedly(unescape(source).replace(r"\/", "/"))
        for pattern in (cls._data_pattern, cls._at_pattern):
            for match in pattern.finditer(normalized):
                coordinates = cls._validated_pair(
                    match.group("latitude"),
                    match.group("longitude"),
                )
                if coordinates is not None:
                    return coordinates
        return None

    @classmethod
    def _extract_pair(cls, value: str) -> tuple[float, float] | None:
        match = cls._pair_pattern.fullmatch(cls._decode_repeatedly(value))
        if match is None:
            return None
        return cls._validated_pair(
            match.group("latitude"),
            match.group("longitude"),
        )

    @staticmethod
    def _validated_pair(latitude: str, longitude: str) -> tuple[float, float] | None:
        parsed_latitude = float(latitude)
        parsed_longitude = float(longitude)
        if not -90 <= parsed_latitude <= 90 or not -180 <= parsed_longitude <= 180:
            return None
        return parsed_latitude, parsed_longitude

    @staticmethod
    def _decode_repeatedly(value: str) -> str:
        decoded = value
        for _ in range(3):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        return decoded


class EnrichJsonMapLocationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: Any
    items_path: str = Field(default="item", min_length=1)
    url_field: str = Field(default="map", min_length=1)
    latitude_field: str = Field(default="latitude", min_length=1)
    longitude_field: str = Field(default="longitude", min_length=1)
    final_url_field: str | None = Field(default=None, min_length=1)
    headers: dict[str, str] | None = None
    timeout: float = Field(default=30, gt=0)
    max_redirects: int = Field(default=10, gt=0, le=50)
    parallel: bool = True
    concurrency: int | None = Field(default=8, gt=0)
    on_url_error: Literal["fail", "continue"] = "fail"
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")

    @field_validator("items_path")
    @classmethod
    def validate_items_path(cls, value: str) -> str:
        parts = value.split(".")
        if any(not part.strip() for part in parts):
            raise ValueError("enrich_json_map_locations items_path contains an empty segment")
        return value

    @field_validator("concurrency")
    @classmethod
    def validate_concurrency(cls, value: int | None, info: Any) -> int | None:
        if value is not None and info.data.get("parallel") is False:
            raise ValueError(
                "enrich_json_map_locations concurrency requires parallel: true"
            )
        return value


@step("enrich_json_map_locations")
class EnrichJsonMapLocationsStep(BaseStep[EnrichJsonMapLocationsConfig]):
    config_model = EnrichJsonMapLocationsConfig

    async def execute(self, context: WorkflowContext) -> Any:
        enriched_data = deepcopy(self.config.data)
        targets = self._find_targets(enriched_data, self.config.items_path.split("."))
        target_urls = [
            (target, url)
            for target in targets
            if (url := self._target_url(target)) is not None
        ]
        unique_urls = list(dict.fromkeys(url for _, url in target_urls))
        resolved = await self._resolve_urls(context, unique_urls)
        for target, url in target_urls:
            location = resolved.get(url)
            if location is None:
                continue
            target[self.config.latitude_field] = location["latitude"]
            target[self.config.longitude_field] = location["longitude"]
            if self.config.final_url_field is not None:
                target[self.config.final_url_field] = location["final_url"]
        context.outputs[self.config.save_as] = enriched_data
        return enriched_data

    def _target_url(self, target: dict[str, Any]) -> str | None:
        value = target.get(self.config.url_field)
        if value is None or not str(value).strip():
            return None
        url = str(value).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            if self.config.on_url_error == "fail":
                raise ValueError(
                    f"enrich_json_map_locations found an invalid map URL: {url}"
                )
            return None
        return url

    async def _resolve_urls(
        self,
        context: WorkflowContext,
        urls: list[str],
    ) -> dict[str, dict[str, Any] | None]:
        results: dict[str, dict[str, Any] | None] = {}
        semaphore = (
            asyncio.Semaphore(self.config.concurrency)
            if self.config.parallel and self.config.concurrency is not None
            else None
        )
        request_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0 Safari/537.36"
            ),
            **(self.config.headers or {}),
        }
        async with httpx.AsyncClient(
            timeout=self.config.timeout,
            follow_redirects=True,
            max_redirects=self.config.max_redirects,
        ) as client:

            async def resolve(url: str) -> tuple[str, dict[str, Any] | None]:
                try:
                    if semaphore is None:
                        location = await self._load_location(
                            context, client, url, request_headers
                        )
                    else:
                        async with semaphore:
                            location = await self._load_location(
                                context, client, url, request_headers
                            )
                    return url, location
                except Exception as error:
                    if self.config.on_url_error == "fail":
                        raise
                    context.storage.setdefault("json_location_enrichment_errors", []).append(
                        {"url": url, "error": str(error)}
                    )
                    return url, None

            if self.config.parallel:
                pairs = await asyncio.gather(*(resolve(url) for url in urls))
            else:
                pairs = [await resolve(url) for url in urls]
        results.update(pairs)
        return results

    async def _load_location(
        self,
        context: WorkflowContext,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        await context.emit_event(
            EventName.REQUEST_STARTED,
            {"transport": "http", "method": "GET", "url": url},
        )
        try:
            response = await client.request("GET", url, headers=headers)
            resolver = ResolveLocationUrlStep(
                ResolveLocationUrlConfig(
                    url=url,
                    headers=self.config.headers,
                    timeout=self.config.timeout,
                    max_redirects=self.config.max_redirects,
                    save_as="location",
                )
            )
            location = resolver._build_result(response, str(response.url))
        except Exception as error:
            await context.emit_event(
                EventName.REQUEST_FINISHED,
                {
                    "transport": "http",
                    "method": "GET",
                    "url": url,
                    "succeeded": False,
                    "error": str(error),
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
            )
            raise
        context.last_response = BrowserResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.text,
        )
        await context.emit_event(
            EventName.REQUEST_FINISHED,
            {
                "transport": "http",
                "method": "GET",
                "url": url,
                "final_url": location["final_url"],
                "succeeded": True,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            },
        )
        return location

    @classmethod
    def _find_targets(
        cls,
        value: Any,
        path: list[str],
        index: int = 0,
    ) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [
                target
                for item in value
                for target in cls._find_targets(item, path, index)
            ]
        if index == len(path):
            return [value] if isinstance(value, dict) else []
        if not isinstance(value, dict) or path[index] not in value:
            return []
        return cls._find_targets(value[path[index]], path, index + 1)


class HttpRequestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = "GET"
    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None
    data: Any = None
    json_body: Any = None
    timeout: float = Field(default=30, gt=0)
    save_as: str | None = None


@step("http_request")
class HttpRequestStep(BaseStep[HttpRequestConfig]):
    config_model = HttpRequestConfig

    async def execute(self, context: WorkflowContext) -> Any:
        method = self.config.method.upper()
        started_at = time.monotonic()
        await context.emit_event(
            EventName.REQUEST_STARTED,
            {"transport": "http", "method": method, "url": self.config.url},
        )
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.request(
                    method,
                    self.config.url,
                    headers=self.config.headers,
                    data=self.config.data,
                    json=self.config.json_body,
                )
        except Exception as error:
            await context.emit_event(
                EventName.REQUEST_FINISHED,
                {
                    "transport": "http",
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
                "transport": "http",
                "method": method,
                "url": self.config.url,
                "succeeded": True,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            },
        )
        context.last_response = BrowserResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.text,
        )
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        result = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
            "raw_body": response.text,
        }
        if self.config.save_as:
            context.outputs[self.config.save_as] = result
        return result
