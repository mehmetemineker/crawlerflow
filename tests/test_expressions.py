from __future__ import annotations

from datetime import date

import pytest

from crawlerflow.expressions import ExpressionEngine, ExpressionError


def test_resolves_nested_variables_and_filters() -> None:
    engine = ExpressionEngine()

    result = engine.resolve(
        "City: {{loop.city|trim|upper}}",
        {"loop": {"city": " istanbul "}},
    )

    assert result == "City: ISTANBUL"


def test_preserves_type_for_a_whole_expression() -> None:
    engine = ExpressionEngine()

    assert engine.resolve("{{delay}}", {"delay": 2}) == 2
    assert engine.resolve("{{today|date('%d.%m.%Y')}}", {"today": date(2026, 8, 4)}) == "04.08.2026"


def test_add_days_supports_relative_date_expressions() -> None:
    engine = ExpressionEngine()

    result = engine.resolve(
        "{{today|add_days(-1)|date('%Y-%m-%d')}}",
        {"today": date(2026, 8, 5)},
    )

    assert result == "2026-08-04"


def test_path_resolution_sanitizes_dynamic_filename_values() -> None:
    engine = ExpressionEngine()

    result = engine.resolve_path(
        "{{output_directory}}/{{district}}_result.html",
        {
            "output_directory": "output/reports",
            "district": "MERKEZ/KÖY",
        },
    )

    assert result == "output/reports/MERKEZ-KÖY_result.html"


def test_path_resolution_preserves_explicit_dynamic_full_paths() -> None:
    engine = ExpressionEngine()

    result = engine.resolve_path(
        "{{output_path}}",
        {"output_path": "output/reports/result.html"},
    )

    assert result == "output/reports/result.html"


def test_path_resolution_ignores_separators_inside_expressions() -> None:
    engine = ExpressionEngine()

    result = engine.resolve_path(
        "output/{{district|replace('/', '-')}}.html",
        {"district": "MERKEZ/KÖY"},
    )

    assert result == "output/MERKEZ-KÖY.html"


def test_rejects_unknown_variables() -> None:
    with pytest.raises(ExpressionError, match="Unknown variable"):
        ExpressionEngine().resolve("{{missing}}", {})

