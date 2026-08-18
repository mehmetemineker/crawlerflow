from __future__ import annotations

import ast
import base64
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

type ExpressionFilter = Callable[..., Any]
_EXPRESSION_PATTERN = re.compile(r"{{\s*(.*?)\s*}}")
_INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or evaluated."""


class ExpressionEngine:
    """Resolve CrawlerFlow expressions without executing workflow code."""

    def __init__(self) -> None:
        self._filters: dict[str, ExpressionFilter] = {
            "upper": lambda value: str(value).upper(),
            "lower": lambda value: str(value).lower(),
            "trim": lambda value: str(value).strip(),
            "replace": lambda value, old, new: str(value).replace(str(old), str(new)),
            "format": lambda value, template: str(template).format(value),
            "date": self._format_date,
            "add_days": self._add_days,
            "timestamp_ms": self._timestamp_ms,
            "urlencode": lambda value: quote_plus(str(value)),
            "json": lambda value: json.dumps(value, ensure_ascii=False),
            "base64": lambda value: base64.b64encode(str(value).encode()).decode(),
        }

    def register_filter(self, name: str, expression_filter: ExpressionFilter) -> None:
        if not name.isidentifier():
            raise ValueError(f"Invalid filter name: {name}")
        self._filters[name] = expression_filter

    def resolve(self, value: Any, variables: Mapping[str, Any]) -> Any:
        if isinstance(value, str):
            return self._resolve_string(value, variables)
        if isinstance(value, list):
            return [self.resolve(item, variables) for item in value]
        if isinstance(value, tuple):
            return tuple(self.resolve(item, variables) for item in value)
        if isinstance(value, dict):
            return {key: self.resolve(item, variables) for key, item in value.items()}
        return value

    def resolve_path(self, value: Any, variables: Mapping[str, Any]) -> Any:
        if not isinstance(value, str):
            return self.resolve(value, variables)
        matches = list(_EXPRESSION_PATTERN.finditer(value))
        if not matches or (len(matches) == 1 and matches[0].span() == (0, len(value))):
            return self.resolve(value, variables)
        masked_template = _EXPRESSION_PATTERN.sub(
            lambda match: " " * len(match.group()),
            value,
        )
        separator_index = max(masked_template.rfind("/"), masked_template.rfind("\\"))
        directory_template = value[: separator_index + 1]
        filename_template = value[separator_index + 1 :]
        directory = self.resolve(directory_template, variables)
        filename = _EXPRESSION_PATTERN.sub(
            lambda match: self._sanitize_filename_value(
                self.evaluate(match.group(1), variables)
            ),
            filename_template,
        )
        return f"{directory}{filename}"

    def evaluate(self, expression: str, variables: Mapping[str, Any]) -> Any:
        parts = self._split_pipeline(expression)
        value = self._lookup(parts[0].strip(), variables)

        for filter_expression in parts[1:]:
            name, arguments = self._parse_filter(filter_expression.strip())
            try:
                expression_filter = self._filters[name]
            except KeyError as error:
                raise ExpressionError(f"Unknown expression filter: {name}") from error
            try:
                value = expression_filter(value, *arguments)
            except (TypeError, ValueError) as error:
                raise ExpressionError(f"Filter '{name}' failed: {error}") from error
        return value

    def _resolve_string(self, value: str, variables: Mapping[str, Any]) -> Any:
        matches = list(_EXPRESSION_PATTERN.finditer(value))
        if not matches:
            return value
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            return self.evaluate(matches[0].group(1), variables)

        return _EXPRESSION_PATTERN.sub(
            lambda match: str(self.evaluate(match.group(1), variables)),
            value,
        )

    @staticmethod
    def _lookup(path: str, variables: Mapping[str, Any]) -> Any:
        current: Any = variables
        for part in path.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                raise ExpressionError(f"Unknown variable: {path}")
        return current

    @staticmethod
    def _split_pipeline(expression: str) -> list[str]:
        parts: list[str] = []
        start = 0
        quote: str | None = None
        depth = 0
        for index, character in enumerate(expression):
            if quote:
                if character == quote and expression[index - 1] != "\\":
                    quote = None
                continue
            if character in {'"', "'"}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < 0:
                    raise ExpressionError("Unbalanced filter parentheses")
            elif character == "|" and depth == 0:
                parts.append(expression[start:index])
                start = index + 1
        if quote or depth:
            raise ExpressionError("Unclosed quote or filter parentheses")
        parts.append(expression[start:])
        return parts

    @staticmethod
    def _parse_filter(expression: str) -> tuple[str, tuple[Any, ...]]:
        if "(" not in expression:
            return expression, ()
        if not expression.endswith(")"):
            raise ExpressionError(f"Invalid filter expression: {expression}")
        name, raw_arguments = expression.split("(", 1)
        raw_arguments = raw_arguments[:-1].strip()
        if not raw_arguments:
            return name.strip(), ()
        try:
            parsed = ast.literal_eval(f"({raw_arguments},)")
        except (SyntaxError, ValueError) as error:
            raise ExpressionError(f"Invalid filter arguments: {expression}") from error
        return name.strip(), parsed

    @staticmethod
    def _format_date(value: Any, date_format: str) -> str:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"'{value}' is not an ISO date") from error
        if not isinstance(value, date):
            raise ValueError("date filter expects a date, datetime, or ISO date string")
        return value.strftime(date_format)

    @staticmethod
    def _add_days(value: Any, days: int) -> date:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"'{value}' is not an ISO date") from error
        if not isinstance(value, date):
            raise ValueError("add_days filter expects a date, datetime, or ISO date string")
        if isinstance(days, bool) or not isinstance(days, int):
            raise ValueError("add_days filter expects an integer day count")
        return value + timedelta(days=days)

    @staticmethod
    def _timestamp_ms(value: Any) -> int:
        if not isinstance(value, datetime):
            raise ValueError("timestamp_ms filter expects a datetime")
        return int(value.timestamp() * 1000)

    @staticmethod
    def _sanitize_filename_value(value: Any) -> str:
        sanitized = _INVALID_FILENAME_CHARACTERS.sub("-", str(value)).rstrip(" .")
        return "_" if sanitized in {"", ".", ".."} else sanitized
