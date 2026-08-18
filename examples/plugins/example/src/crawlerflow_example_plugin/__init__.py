from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import BaseStep
from crawlerflow.plugins import PluginRegistrationContext


class SetOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[A-Za-z_]\w*$")
    value: Any


class ExampleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str = "["
    suffix: str = "]"


class SetOutputStep(BaseStep[SetOutputConfig]):
    config_model = SetOutputConfig

    async def execute(self, context: WorkflowContext) -> Any:
        context.outputs[self.config.key] = self.config.value
        return self.config.value


class DecorateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")


class DecorateStep(BaseStep[DecorateConfig]):
    config_model = DecorateConfig

    async def execute(self, context: WorkflowContext) -> str:
        settings = context.plugin_settings["example"]
        if not isinstance(settings, ExampleSettings):
            raise RuntimeError("Example plugin settings are not initialized")
        result = f"{settings.prefix}{self.config.value}{settings.suffix}"
        context.outputs[self.config.save_as] = result
        return result


class ExamplePlugin:
    name = "example"
    settings_model = ExampleSettings

    def register(self, context: PluginRegistrationContext) -> None:
        context.registry.register("set_output", SetOutputStep)
        context.registry.register("decorate", DecorateStep)
        context.expression_engine.register_filter(
            "surround",
            lambda value, prefix, suffix: f"{prefix}{value}{suffix}",
        )


__all__ = ["ExamplePlugin", "ExampleSettings"]
