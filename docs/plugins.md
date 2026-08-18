# Plugins

Plugins extend a runner with custom steps, expression filters, and lifecycle event subscribers.
Only plugins explicitly passed to `WorkflowRunner` or listed in workflow YAML are activated.

## Plugin contract

```python
from pydantic import BaseModel, ConfigDict

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.plugins import PluginRegistrationContext


class ExampleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    timeout: float = 10


class ExamplePlugin:
    name = "example"
    settings_model = ExampleSettings

    def register(self, context: PluginRegistrationContext) -> None:
        context.registry.register("custom_step", CustomStep)
        context.expression_engine.register_filter("reverse", lambda value: str(value)[::-1])
        context.event_bus.subscribe(EventName.STEP_FINISHED, handle_step)

    async def startup(self, context: WorkflowContext) -> None:
        settings = context.plugin_settings[self.name]
        context.storage["client"] = await create_client(settings.endpoint, settings.timeout)

    async def shutdown(self, context: WorkflowContext) -> None:
        await context.storage["client"].close()
```

Applications may inject plugin instances directly:

```python
runner = WorkflowRunner(plugins=[ExamplePlugin()])
```

Each runner gets an isolated step registry, so plugin registrations do not leak into other runner
instances.

## YAML settings

Plugins without settings keep the short form:

```yaml
plugins:
  - example
```

Use the object form to pass workflow-specific settings:

```yaml
plugins:
  - name: example
    settings:
      endpoint: https://api.example.com
      timeout: 20
```

A plugin opts into settings by exposing a Pydantic `settings_model`. CrawlerFlow validates settings
while loading the workflow and stores the resulting model instance in
`context.plugin_settings[plugin_name]`. Non-empty settings are rejected when a plugin does not
declare a model. Plugin names must remain unique within one workflow.

## Runtime lifecycle

`startup` and `shutdown` are optional and may be synchronous or asynchronous. Both receive the
current `WorkflowContext`, including variables, outputs, plugin settings, storage, browser adapter,
and base path.

The runner applies this order for every execution:

1. Load, register, and validate configured plugins.
2. Call `startup` in plugin registration order.
3. Execute the workflow and publish its lifecycle events.
4. Call `shutdown` in reverse registration order.
5. Detach the event logger and close the browser adapter.

If startup fails, plugins that already started are shut down before the error is returned. Every
shutdown hook is attempted even if another shutdown hook fails. A shutdown failure after a workflow
failure is attached to the original error rather than replacing it.

## Package entry points

External packages expose plugin classes or instances through the `crawlerflow.plugins` entry-point
group:

```toml
[project.entry-points."crawlerflow.plugins"]
example = "example_package.plugin:ExamplePlugin"
```

The workflow opts in by entry-point name:

```yaml
version: 1
workflow:
  name: plugin-workflow
plugins:
  - example
steps:
  - custom_step: {}
```

CrawlerFlow rejects missing plugins, duplicate plugin names, and entry points whose loaded plugin
name differs from the configured entry-point name.

Discover installed entry points without importing or activating plugin code:

```bash
python -m crawlerflow list-plugins
```

The command displays each plugin name, import target, and owning Python distribution.

## Example package

`examples/plugins/example` is a separately installable reference package. It registers a custom
`set_output` step and `surround` expression filter:

```bash
python -m pip install -e . -e examples/plugins/example
python -m crawlerflow list-plugins
python -m crawlerflow run examples/plugins/example/workflow.yaml
```
