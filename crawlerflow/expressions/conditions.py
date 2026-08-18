"""Declarative condition evaluation for control-flow steps."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConditionError(ValueError):
    """Raised when a condition cannot be evaluated."""


class ConditionOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    IN = "in"
    NOT_IN = "not_in"
    TRUTHY = "truthy"
    FALSY = "falsy"


class ConditionExpression(BaseModel):
    """Structured comparison used by the `if` step."""

    model_config = ConfigDict(extra="forbid")

    left: Any
    operator: ConditionOperator
    right: Any = None


class ConditionEngine:
    """Evaluate booleans and safe declarative comparisons."""

    def evaluate(self, condition: Any) -> bool:
        if isinstance(condition, Mapping):
            condition = ConditionExpression.model_validate(condition)
        if not isinstance(condition, ConditionExpression):
            return bool(condition)

        left = condition.left
        right = condition.right
        operator = condition.operator
        if operator is ConditionOperator.EQ:
            return left == right
        if operator is ConditionOperator.NE:
            return left != right
        if operator is ConditionOperator.GT:
            return left > right
        if operator is ConditionOperator.GTE:
            return left >= right
        if operator is ConditionOperator.LT:
            return left < right
        if operator is ConditionOperator.LTE:
            return left <= right
        if operator is ConditionOperator.CONTAINS:
            return self._contains(left, right)
        if operator is ConditionOperator.NOT_CONTAINS:
            return not self._contains(left, right)
        if operator is ConditionOperator.IN:
            return self._contains(right, left)
        if operator is ConditionOperator.NOT_IN:
            return not self._contains(right, left)
        if operator is ConditionOperator.TRUTHY:
            return bool(left)
        if operator is ConditionOperator.FALSY:
            return not left
        raise ConditionError(f"Unsupported condition operator: {operator}")

    @staticmethod
    def _contains(container: Any, item: Any) -> bool:
        if isinstance(container, (str, Collection)):
            return item in container
        raise ConditionError(f"Value of type {type(container).__name__} does not support contains")
