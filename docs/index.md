# Crawlerflow

[![PyPI version](https://img.shields.io/pypi/v/crawlerflow.svg)](https://pypi.org/project/crawlerflow/)
[![Python versions](https://img.shields.io/pypi/pyversions/crawlerflow.svg)](https://pypi.org/project/crawlerflow/)
[![License](https://img.shields.io/pypi/l/crawlerflow.svg)](https://github.com/mehmetemineker/crawlerflow/blob/main/LICENSE)

Crawlerflow is a declarative, YAML-based workflow engine for browser automation and web
scraping. Workflows describe what should happen; adapters and steps decide how it happens.

```yaml
version: 1

workflow:
  name: basic-example

variables:
  project: Crawlerflow
  output_file: output/hello.txt

steps:
  - log:
      message: "Starting {{project}}"

  - save_text:
      path: "{{output_file}}"
      content: "{{project|upper}} is running."
```

```bash
pip install crawlerflow
crawlerflow run workflow.yaml
```

## Feature overview

- Versioned YAML workflow loading and validation
- Browser-independent adapter contract and a lazy-starting Pydoll implementation
- Extensible step registry
- Isolated plugin API with typed YAML settings, lifecycle hooks, steps, filters, and subscribers
- Async workflow executor and event bus
- Variable interpolation and a built-in expression engine
- Per-run `today` and `now` date variables
- Nested `foreach`, `foreach_date`, `foreach_select`, and declarative `if` control flow
- Reusable parameterized workflow macros
- Per-step retry and continue/fail error policies
- JSON Lines workflow, step, retry, and request event logging
- Built-in navigation, interaction, cookies, downloads, screenshots, and selective HTML output
- `run`, `validate`, `list-steps`, `list-plugins`, and `doctor` CLI commands

## Where to go next

| Guide | Contents |
| --- | --- |
| [Getting started](getting-started.md) | Installation, CLI commands, and running workflows |
| [Control flow](control-flow.md) | Loops, conditions, parallel iteration, and macros |
| [Runtime variables](runtime-variables.md) | Built-in `today` and `now` values and date filters |
| [HTML output](html-output.md) | Selector filtering, pretty printing, and link enrichment |
| [HTTP requests](http-requests.md) | Browser-free requests and map coordinate resolution |
| [Retries and logging](retries-and-logging.md) | Retry policies, error handling, and event logs |
| [Architecture](architecture.md) | Engine boundaries and extension points |
| [Plugins](plugins.md) | Building and packaging external extensions |
| [Pydoll adapter](pydoll.md) | Browser configuration and lifecycle |
