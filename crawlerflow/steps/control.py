"""Nested workflow control-flow steps."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import BaseStep, step
from crawlerflow.workflow.models import StepDefinition


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    item_name: str = Field(default="item", alias="as", pattern=r"^[A-Za-z_]\w*$")
    parallel: bool = False
    concurrency: int | None = Field(default=None, gt=0)

    @field_validator("item_name")
    @classmethod
    def reject_reserved_loop_names(cls, value: str) -> str:
        reserved = {
            "index",
            "index0",
            "first",
            "last",
            "length",
            "key",
            "value",
            "parent",
            "text",
            "original_text",
            "disabled",
            "selected",
            "option_index",
            "iso",
        }
        if value in reserved:
            raise ValueError(f"'{value}' is reserved loop metadata")
        return value

    @model_validator(mode="after")
    def validate_concurrency(self) -> LoopConfig:
        if self.concurrency is not None and not self.parallel:
            raise ValueError("loop concurrency requires parallel: true")
        return self


@dataclass(slots=True, frozen=True)
class _LoopIteration:
    item: Any
    index: int
    metadata: dict[str, Any]


async def _execute_loop_iterations(
    context: WorkflowContext,
    *,
    item_name: str,
    steps: list[StepDefinition],
    iterations: list[_LoopIteration],
    parallel: bool,
    concurrency: int | None,
) -> None:
    length = len(iterations)
    if not parallel:
        for iteration in iterations:
            await _execute_loop_iteration(
                context,
                item_name=item_name,
                steps=steps,
                iteration=iteration,
                length=length,
            )
        return

    if context.browser is not None:
        raise ValueError("parallel loop execution requires a browser-free workflow")

    baseline = context.fork()
    semaphore = asyncio.Semaphore(concurrency) if concurrency is not None else None

    async def execute(iteration: _LoopIteration) -> WorkflowContext:
        child = context.fork()
        if semaphore is None:
            return await _execute_loop_iteration(
                child,
                item_name=item_name,
                steps=steps,
                iteration=iteration,
                length=length,
            )
        async with semaphore:
            return await _execute_loop_iteration(
                child,
                item_name=item_name,
                steps=steps,
                iteration=iteration,
                length=length,
            )

    results = await asyncio.gather(
        *(execute(iteration) for iteration in iterations),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    for result in results:
        _merge_parallel_context(context, result, baseline)


async def _execute_loop_iteration(
    context: WorkflowContext,
    *,
    item_name: str,
    steps: list[StepDefinition],
    iteration: _LoopIteration,
    length: int,
) -> WorkflowContext:
    with context.loop_scope(
        item=iteration.item,
        item_name=item_name,
        index=iteration.index,
        length=length,
        metadata=iteration.metadata,
    ):
        await context.execute_steps(steps)
    return context


def _merge_parallel_context(
    target: WorkflowContext,
    source: WorkflowContext,
    baseline: WorkflowContext,
) -> None:
    _merge_mapping_changes(target.outputs, source.outputs, baseline.outputs)
    _merge_mapping_changes(target.storage, source.storage, baseline.storage)
    _merge_mapping_changes(target.cookies, source.cookies, baseline.cookies)
    _merge_mapping_changes(target.headers, source.headers, baseline.headers)
    if source.last_response is not baseline.last_response:
        target.last_response = source.last_response
    if source.last_html != baseline.last_html:
        target.last_html = source.last_html


def _merge_mapping_changes(
    target: dict[str, Any],
    source: dict[str, Any],
    baseline: dict[str, Any],
) -> None:
    for key, value in source.items():
        if key not in baseline or value != baseline[key]:
            target[key] = value


class ForeachConfig(LoopConfig):
    items: Any
    steps: list[StepDefinition]


@step("foreach")
class ForeachStep(BaseStep[ForeachConfig]):
    config_model = ForeachConfig
    deferred_fields = frozenset({"steps"})
    nested_step_fields = frozenset({"steps"})

    async def execute(self, context: WorkflowContext) -> None:
        entries = self._entries(self.config.items)
        iterations = [
            _LoopIteration(
                item=item,
                index=index,
                metadata={} if key is None else {"key": key, "value": item},
            )
            for index, (key, item) in enumerate(entries)
        ]
        await _execute_loop_iterations(
            context,
            item_name=self.config.item_name,
            steps=self.config.steps,
            iterations=iterations,
            parallel=self.config.parallel,
            concurrency=self.config.concurrency,
        )

    @staticmethod
    def _entries(items: Any) -> list[tuple[Any | None, Any]]:
        if isinstance(items, Mapping):
            return list(items.items())
        if isinstance(items, (list, tuple)):
            return [(None, item) for item in items]
        raise ValueError("foreach items must resolve to a list, tuple, or mapping")


class ForeachDateConfig(LoopConfig):
    item_name: str = Field(default="date", alias="as", pattern=r"^[A-Za-z_]\w*$")
    start: date
    end: date | None = None
    step_days: int = Field(default=1, gt=0)
    steps: list[StepDefinition]

    @model_validator(mode="after")
    def validate_range(self) -> ForeachDateConfig:
        if self.end is not None and self.end < self.start:
            raise ValueError("foreach_date end must be on or after start")
        return self


@step("foreach_date")
class ForeachDateStep(BaseStep[ForeachDateConfig]):
    config_model = ForeachDateConfig
    deferred_fields = frozenset({"steps"})
    nested_step_fields = frozenset({"steps"})

    async def execute(self, context: WorkflowContext) -> None:
        end = self.config.end or self.config.start
        length = ((end - self.config.start).days // self.config.step_days) + 1
        iterations = []
        for index in range(length):
            current = self.config.start + timedelta(days=index * self.config.step_days)
            iterations.append(
                _LoopIteration(
                    item=current,
                    index=index,
                    metadata={"iso": current.isoformat()},
                )
            )
        await _execute_loop_iterations(
            context,
            item_name=self.config.item_name,
            steps=self.config.steps,
            iterations=iterations,
            parallel=self.config.parallel,
            concurrency=self.config.concurrency,
        )


class ForeachSelectConfig(LoopConfig):
    item_name: str = Field(default="option", alias="as", pattern=r"^[A-Za-z_]\w*$")
    selector: str = Field(min_length=1)
    match_index: int = Field(default=0, ge=0)
    content: str | None = None
    include_disabled: bool = False
    include_empty: bool = True
    exclude_values: list[str] = Field(default_factory=list)
    text_overrides: dict[str, str] = Field(default_factory=dict)
    steps: list[StepDefinition]

    @field_validator("exclude_values", mode="before")
    @classmethod
    def normalize_excluded_values(cls, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item) for item in value]
        return value

    @field_validator("text_overrides", mode="before")
    @classmethod
    def normalize_text_overrides(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): str(text) for key, text in value.items()}
        if isinstance(value, list):
            overrides: dict[str, str] = {}
            for group in value:
                if not isinstance(group, Mapping) or "values" not in group or "text" not in group:
                    raise ValueError(
                        "text_overrides groups require 'values' and 'text' fields"
                    )
                values = group["values"]
                if not isinstance(values, (list, tuple, set)):
                    values = [values]
                for option_value in values:
                    overrides[str(option_value)] = str(group["text"])
            return overrides
        return value


@step("foreach_select")
class ForeachSelectStep(BaseStep[ForeachSelectConfig]):
    config_model = ForeachSelectConfig
    deferred_fields = frozenset({"steps"})
    nested_step_fields = frozenset({"steps"})

    async def execute(self, context: WorkflowContext) -> None:
        browser = None
        if self.config.content is None:
            browser = context.require_browser()
            options = await browser.evaluate(
                self._options_script(self.config.selector, self.config.match_index)
            )
        else:
            options = self._options_from_html(
                self.config.content,
                self.config.selector,
                self.config.match_index,
            )
        if not isinstance(options, list):
            raise ValueError("foreach_select could not read select options")
        filtered_options = [option for option in options if self._include(option)]
        length = len(filtered_options)
        iterations: list[_LoopIteration] = []
        for index, option in enumerate(filtered_options):
            value = str(option.get("value", ""))
            original_text = str(option.get("text", ""))
            text_template = self.config.text_overrides.get(value)
            text = (
                original_text
                if text_template is None
                else text_template.replace("{original_text}", original_text).replace(
                    "{value}", value
                )
            )
            if browser is not None:
                if self.config.parallel:
                    raise ValueError(
                        "parallel foreach_select requires supplied content without a browser"
                    )
                await browser.select(self.config.selector, value)
                with context.loop_scope(
                    item=value,
                    item_name=self.config.item_name,
                    index=index,
                    length=length,
                    metadata={
                        "value": value,
                        "text": text,
                        "original_text": original_text,
                        "disabled": bool(option.get("disabled", False)),
                        "selected": bool(option.get("selected", False)),
                        "option_index": int(option.get("index", index)),
                    },
                ):
                    await context.execute_steps(self.config.steps)
                continue
            iterations.append(
                _LoopIteration(
                    item=value,
                    index=index,
                    metadata={
                        "value": value,
                        "text": text,
                        "original_text": original_text,
                        "disabled": bool(option.get("disabled", False)),
                        "selected": bool(option.get("selected", False)),
                        "option_index": int(option.get("index", index)),
                    },
                )
            )
        if browser is None:
            await _execute_loop_iterations(
                context,
                item_name=self.config.item_name,
                steps=self.config.steps,
                iterations=iterations,
                parallel=self.config.parallel,
                concurrency=self.config.concurrency,
            )

    @staticmethod
    def _options_from_html(
        content: str,
        selector: str,
        match_index: int = 0,
    ) -> list[dict[str, Any]]:
        parser = _HtmlSelectOptionsParser(selector, match_index)
        parser.feed(content)
        parser.close()
        return parser.selected_options()

    def _include(self, option: Any) -> bool:
        if not isinstance(option, Mapping):
            raise ValueError("foreach_select options must be mappings")
        if not self.config.include_disabled and option.get("disabled"):
            return False
        value = str(option.get("value", ""))
        if value in self.config.exclude_values:
            return False
        return self.config.include_empty or bool(value)

    @staticmethod
    def _options_script(selector: str, match_index: int = 0) -> str:
        encoded_selector = json.dumps(selector)
        return (
            f"const selector = {encoded_selector}; "
            f"const matchIndex = {match_index}; "
            "let select; "
            "if (selector.startsWith('/') || selector.startsWith('./')) { "
            "const result = document.evaluate(selector, document, null, "
            "XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); "
            "select = result.snapshotItem(matchIndex); "
            "} else { select = document.querySelectorAll(selector)[matchIndex]; } "
            "if (!select) throw new Error('Select element not found'); "
            "if (!(select instanceof HTMLSelectElement)) "
            "throw new Error('Element is not a <select>'); "
            "return Array.from(select.options).map((option, index) => ({ "
            "value: option.value, text: option.text, disabled: option.disabled, "
            "selected: option.selected, index }));"
        )


@dataclass(slots=True, frozen=True)
class _HtmlSelectorPart:
    tag: str | None
    element_id: str | None
    classes: frozenset[str]
    attributes: tuple[tuple[str, str], ...]

    _identifier = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
    _attribute = re.compile(
        r"\[\s*([A-Za-z_:][A-Za-z0-9:_.-]*)\s*=\s*"
        r'(?:"([^"]*)"|\'([^\']*)\'|([^\]\s]+))\s*\]'
    )

    @classmethod
    def parse(cls, source: str) -> _HtmlSelectorPart:
        tag_match = cls._identifier.match(source)
        index = 0
        if tag_match is not None:
            index = tag_match.end()
        element_id: str | None = None
        classes: set[str] = set()
        attributes: list[tuple[str, str]] = []
        while index < len(source):
            prefix = source[index]
            if prefix in {"#", "."}:
                identifier = cls._identifier.match(source, index + 1)
                if identifier is None:
                    break
                value = identifier.group()
                if prefix == "#":
                    if element_id is not None:
                        raise ValueError(
                            "Browser-free foreach_select selector cannot contain multiple IDs"
                        )
                    element_id = value
                else:
                    classes.add(value)
                index = identifier.end()
                continue
            if prefix == "[":
                attribute = cls._attribute.match(source, index)
                if attribute is None:
                    break
                value = next(
                    group for group in attribute.groups()[1:] if group is not None
                )
                attributes.append((attribute.group(1).lower(), value))
                index = attribute.end()
                continue
            break
        if index != len(source) or not source:
            raise ValueError(
                "Browser-free foreach_select content supports tag, #id, .class, exact "
                "attribute, and descendant selectors"
            )
        return cls(
            tag=tag_match.group().lower() if tag_match is not None else None,
            element_id=element_id,
            classes=frozenset(classes),
            attributes=tuple(attributes),
        )

    def matches(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if self.tag is not None and self.tag != tag:
            return False
        if self.element_id is not None and attributes.get("id") != self.element_id:
            return False
        element_classes = frozenset(attributes.get("class", "").split())
        if not self.classes <= element_classes:
            return False
        return all(attributes.get(name) == value for name, value in self.attributes)


@dataclass(slots=True, frozen=True)
class _HtmlSelectSelector:
    parts: tuple[_HtmlSelectorPart, ...]

    @classmethod
    def parse(cls, selector: str) -> _HtmlSelectSelector:
        parts = tuple(
            _HtmlSelectorPart.parse(part) for part in cls._split_parts(selector.strip())
        )
        if not parts:
            raise ValueError("Browser-free foreach_select selector cannot be empty")
        if parts[-1].tag not in {None, "select"}:
            raise ValueError("Browser-free foreach_select selector must target a <select>")
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

    @staticmethod
    def _split_parts(selector: str) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        bracket_depth = 0
        quote: str | None = None
        for character in selector:
            if quote is not None:
                current.append(character)
                if character == quote:
                    quote = None
                continue
            if character in {'"', "'"} and bracket_depth:
                quote = character
                current.append(character)
                continue
            if character == "[":
                bracket_depth += 1
                current.append(character)
                continue
            if character == "]":
                bracket_depth -= 1
                current.append(character)
                continue
            if character.isspace() and bracket_depth == 0:
                if current:
                    parts.append("".join(current))
                    current = []
                continue
            current.append(character)
        if current:
            parts.append("".join(current))
        if quote is not None or bracket_depth != 0:
            raise ValueError("Browser-free foreach_select selector has invalid attribute syntax")
        return parts


class _HtmlSelectOptionsParser(HTMLParser):
    _void_tags = frozenset(
        {
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
    )

    def __init__(self, selector: str, match_index: int = 0) -> None:
        super().__init__(convert_charrefs=True)
        self.selector = _HtmlSelectSelector.parse(selector)
        self.match_index = match_index
        self.options: list[dict[str, Any]] = []
        self.ancestors: list[tuple[str, list[tuple[str, str | None]]]] = []
        self._matched = False
        self._candidate_index = 0
        self._inside_select = False
        self._current_option: dict[str, Any] | None = None
        self._option_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        is_candidate = (
            not self._inside_select
            and not self._matched
            and tag == "select"
            and self.selector.matches(tag, attrs, self.ancestors)
        )
        if is_candidate:
            if self._candidate_index == self.match_index:
                self._matched = True
                self._inside_select = True
            self._candidate_index += 1
        elif self._inside_select and tag == "option":
            self._finish_option()
            attributes = {name.lower(): value for name, value in attrs}
            self._current_option = {
                "value": attributes.get("value"),
                "disabled": "disabled" in attributes,
                "selected": "selected" in attributes,
            }
            self._option_text = []
        if tag not in self._void_tags:
            self.ancestors.append((tag, attrs))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._inside_select and tag == "option":
            self._finish_option()
        if self._inside_select and tag == "select":
            self._finish_option()
            self._inside_select = False
        for index in range(len(self.ancestors) - 1, -1, -1):
            if self.ancestors[index][0] == tag:
                del self.ancestors[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._current_option is not None:
            self._option_text.append(data)

    def selected_options(self) -> list[dict[str, Any]]:
        self._finish_option()
        if not self._matched:
            raise ValueError("foreach_select could not find select in supplied content")
        return self.options

    def _finish_option(self) -> None:
        if self._current_option is None:
            return
        text = " ".join("".join(self._option_text).split())
        value = self._current_option["value"]
        self.options.append(
            {
                "value": text if value is None else value,
                "text": text,
                "disabled": self._current_option["disabled"],
                "selected": self._current_option["selected"],
                "index": len(self.options),
            }
        )
        self._current_option = None
        self._option_text = []


class IfConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    condition: Any
    then_steps: list[StepDefinition] = Field(alias="then", min_length=1)
    else_steps: list[StepDefinition] = Field(default_factory=list, alias="else")


@step("if")
class IfStep(BaseStep[IfConfig]):
    config_model = IfConfig
    deferred_fields = frozenset({"then", "else"})
    nested_step_fields = frozenset({"then", "else"})

    async def execute(self, context: WorkflowContext) -> None:
        branch = (
            self.config.then_steps
            if context.condition_engine.evaluate(self.config.condition)
            else self.config.else_steps
        )
        if branch:
            await context.execute_steps(branch)


class RunMacroConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict, alias="with")


@step("run_macro")
class RunMacroStep(BaseStep[RunMacroConfig]):
    config_model = RunMacroConfig

    async def execute(self, context: WorkflowContext) -> None:
        try:
            macro_steps = context.macros[self.config.name]
        except KeyError as error:
            raise ValueError(f"Unknown macro: {self.config.name}") from error
        with context.macro_scope(self.config.name, self.config.arguments):
            await context.execute_steps(macro_steps)
