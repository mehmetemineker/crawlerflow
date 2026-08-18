from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from crawlerflow.engine.context import WorkflowContext


class Step(Protocol):
    async def execute(self, context: WorkflowContext) -> Any: ...


class BaseStep[StepConfig: BaseModel]:
    """Base class for validated workflow steps."""

    config_model: ClassVar[type[BaseModel]]
    deferred_fields: ClassVar[frozenset[str]] = frozenset()
    nested_step_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: StepConfig) -> None:
        self.config = config


class StepRegistry:
    """Map YAML step names to independently implemented step classes."""

    def __init__(self) -> None:
        self._steps: dict[str, type[BaseStep[Any]]] = {}

    def register(self, name: str, step_class: type[BaseStep[Any]]) -> None:
        if not name.isidentifier():
            raise ValueError(f"Invalid step name: {name}")
        if name in self._steps:
            raise ValueError(f"Step is already registered: {name}")
        self._steps[name] = step_class

    def create(self, name: str, config: dict[str, Any]) -> Step:
        try:
            step_class = self._steps[name]
        except KeyError as error:
            raise ValueError(f"Unknown step: {name}") from error
        validated_config = step_class.config_model.model_validate(config)
        return step_class(validated_config)

    def validate_structure(self, name: str, config: dict[str, Any]) -> None:
        """Validate step identity and top-level fields before expressions resolve."""

        try:
            config_model = self._steps[name].config_model
        except KeyError as error:
            raise ValueError(f"Unknown step: {name}") from error

        fields = config_model.model_fields
        accepted_names = {
            field_info.alias or field_name
            for field_name, field_info in fields.items()
        } | set(fields)
        missing = sorted(
            field_info.alias or field_name
            for field_name, field_info in fields.items()
            if field_info.is_required()
            and field_name not in config
            and (field_info.alias is None or field_info.alias not in config)
        )
        if missing:
            raise ValueError(f"Step '{name}' is missing required fields: {', '.join(missing)}")

        if config_model.model_config.get("extra") == "forbid":
            unknown = sorted(set(config) - accepted_names)
            if unknown:
                raise ValueError(f"Step '{name}' has unknown fields: {', '.join(unknown)}")

    def names(self) -> Iterable[str]:
        return tuple(sorted(self._steps))

    def deferred_fields(self, name: str) -> frozenset[str]:
        try:
            return self._steps[name].deferred_fields
        except KeyError as error:
            raise ValueError(f"Unknown step: {name}") from error

    def nested_step_fields(self, name: str) -> frozenset[str]:
        try:
            return self._steps[name].nested_step_fields
        except KeyError as error:
            raise ValueError(f"Unknown step: {name}") from error

    def include(self, other: StepRegistry) -> None:
        """Add registrations that do not already exist in this registry."""

        for name, step_class in other._steps.items():
            self._steps.setdefault(name, step_class)


default_registry = StepRegistry()


def step(name: str, *, registry: StepRegistry = default_registry):
    """Register a step class under its YAML name."""

    def decorator(step_class: type[BaseStep[Any]]) -> type[BaseStep[Any]]:
        registry.register(name, step_class)
        return step_class

    return decorator
