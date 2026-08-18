from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = None


class BrowserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["pydoll"] | None = None
    headless: bool = True
    binary_location: Path | None = None
    arguments: list[str] = Field(default_factory=list)
    start_timeout: int = Field(default=10, gt=0)
    default_wait_timeout: float = Field(default=10, gt=0)
    network_idle_period: float = Field(default=0.5, gt=0)
    download_directory: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def select_default_engine(cls, value: Any) -> Any:
        if isinstance(value, dict) and value and "engine" not in value:
            return {"engine": "pydoll", **value}
        return value


class RetryPolicy(BaseModel):
    """Retry settings attached to one workflow step."""

    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(default=3, ge=1)
    delay: float = Field(default=0, ge=0)
    backoff: float = Field(default=1, ge=1)
    max_delay: float | None = Field(default=None, ge=0)

    def delay_for(self, attempt: int) -> float:
        """Return delay before a one-based retry attempt."""

        delay = self.delay * (self.backoff ** max(0, attempt - 2))
        return min(delay, self.max_delay) if self.max_delay is not None else delay


class LoggingSettings(BaseModel):
    """Console and JSON Lines event log configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    console: bool = False
    path: Path | None = None
    mode: Literal["append", "overwrite"] = "append"
    include_payload: bool = True

    @model_validator(mode="before")
    @classmethod
    def enable_when_path_is_set(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and (value.get("path") or value.get("console"))
            and "enabled" not in value
        ):
            return {"enabled": True, **value}
        return value

    @model_validator(mode="after")
    def require_path_when_enabled(self) -> LoggingSettings:
        if self.enabled and self.path is None and not self.console:
            raise ValueError("logging requires a path or console: true when enabled")
        return self


class PluginDefinition(BaseModel):
    """One enabled plugin and its workflow-specific settings."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def parse_short_form(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"name": value}
        return value


class StepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    config: dict[str, Any]
    retry: RetryPolicy | None = None
    on_error: Literal["fail", "continue"] = "fail"
    save_error_as: str | None = Field(default=None, pattern=r"^[A-Za-z_]\w*$")

    @model_validator(mode="before")
    @classmethod
    def parse_yaml_step(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("Each step must be a mapping")
        metadata_names = {"retry", "on_error", "save_error_as"}
        operations = {
            key: item
            for key, item in value.items()
            if key not in metadata_names
        }
        if len(operations) != 1:
            raise ValueError("Each step must contain exactly one step name")
        name, config = next(iter(operations.items()))
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError(f"Step '{name}' configuration must be a mapping")
        return {
            "name": name,
            "config": config,
            "retry": value.get("retry"),
            "on_error": value.get("on_error", "fail"),
            "save_error_as": value.get("save_error_as"),
        }


class WorkflowDocument(BaseModel):
    """Validated representation of a version 1 workflow file."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    workflow: WorkflowMetadata
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    plugins: list[PluginDefinition] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    macros: dict[str, list[StepDefinition]] = Field(default_factory=dict)
    steps: list[StepDefinition] = Field(min_length=1)
    outputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("plugins")
    @classmethod
    def validate_plugin_names(
        cls,
        plugins: list[PluginDefinition],
    ) -> list[PluginDefinition]:
        names = [plugin.name for plugin in plugins]
        if len(names) != len(set(names)):
            raise ValueError("Plugin names must be unique")
        return plugins

    @field_validator("macros")
    @classmethod
    def validate_macro_names(
        cls,
        macros: dict[str, list[StepDefinition]],
    ) -> dict[str, list[StepDefinition]]:
        invalid_names = [name for name in macros if not name.isidentifier()]
        if invalid_names:
            raise ValueError(f"Invalid macro name: {invalid_names[0]}")
        return macros
