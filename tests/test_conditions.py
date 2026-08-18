from __future__ import annotations

import pytest

from crawlerflow.expressions import ConditionEngine, ConditionError


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"left": 5, "operator": "eq", "right": 5}, True),
        ({"left": 5, "operator": "ne", "right": 4}, True),
        ({"left": 5, "operator": "gt", "right": 4}, True),
        ({"left": "crawlerflow", "operator": "contains", "right": "flow"}, True),
        ({"left": "flow", "operator": "in", "right": ["flow", "crawl"]}, True),
        ({"left": [], "operator": "falsy"}, True),
    ],
)
def test_evaluates_declarative_conditions(condition: object, expected: bool) -> None:
    assert ConditionEngine().evaluate(condition) is expected


def test_rejects_contains_on_non_collection() -> None:
    with pytest.raises(ConditionError, match="does not support contains"):
        ConditionEngine().evaluate({"left": 10, "operator": "contains", "right": 1})

