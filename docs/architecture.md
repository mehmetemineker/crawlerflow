# Architecture

Crawlerflow separates declarative workflow intent from execution details.

```text
YAML workflow
    -> WorkflowLoader / Pydantic models
    -> WorkflowRunner
    -> ExpressionEngine
    -> StepRegistry / WorkflowExecutor
    -> BrowserAdapter
```

## Extension boundaries

- A browser backend implements `BrowserAdapter`; the engine never imports a concrete browser.
- A workflow operation subclasses `BaseStep` and registers a unique YAML name.
- A plugin receives isolated registry, expression engine, and event bus extension points.
- Expression filters are registered on `ExpressionEngine` without changing its parser.

The runner accepts injected browser adapters for tests and applications, while the browser factory
constructs configured adapters for CLI workflows. This leaves room for Pydoll, Playwright, and
Selenium integrations without coupling the executor to a concrete browser.

## Plugins

`PluginManager` registers application-provided plugin instances and loads workflow-opted package
entry points from the `crawlerflow.plugins` group. Every runner copies built-in steps into its own
registry before registering plugins, preventing extension state from leaking between runners. It
also runs optional sync or async plugin startup hooks in registration order and shutdown hooks in
reverse order around each workflow execution. Plugins may expose a Pydantic `settings_model`; the
runner validates workflow-specific YAML settings before execution and places typed settings in the
workflow context.

## Pydoll lifecycle

`PydollBrowserAdapter` starts Chromium lazily on the first browser operation. It translates CSS
or XPath selectors through `Tab.query()`, tracks CDP network request lifecycle events for
`wait_network`, and uses Pydoll's browser-context request client for authenticated HTTP calls.
The runner closes the active adapter after every workflow, including failed executions.

## Execution policies and logs

`StepDefinition` separates operation configuration from execution metadata. `WorkflowExecutor`
applies retry and error policies around validated step execution and publishes lifecycle events.
`JsonEventLogger` subscribes for one workflow run and writes those events as JSON Lines without
coupling steps to a logging backend.
