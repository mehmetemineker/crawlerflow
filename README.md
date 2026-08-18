# CrawlerFlow

[![PyPI version](https://img.shields.io/pypi/v/crawlerflow.svg)](https://pypi.org/project/crawlerflow/)
[![Python versions](https://img.shields.io/pypi/pyversions/crawlerflow.svg)](https://pypi.org/project/crawlerflow/)
[![License](https://img.shields.io/pypi/l/crawlerflow.svg)](https://github.com/mehmetemineker/crawlerflow/blob/main/LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/crawlerflow.svg)](https://pypi.org/project/crawlerflow/)
[![Wheel](https://img.shields.io/pypi/wheel/crawlerflow.svg)](https://pypi.org/project/crawlerflow/#files)
[![Documentation](https://img.shields.io/badge/docs-github.io-blue.svg)](https://mehmetemineker.github.io/crawlerflow/)

CrawlerFlow is a declarative, YAML-based workflow engine for browser automation and web
scraping. Workflows describe what should happen; adapters and steps decide how it happens.

📖 **[Read the documentation](https://mehmetemineker.github.io/crawlerflow/)**

## Installation

```bash
pip install crawlerflow
```

Include browser support to run Pydoll-backed workflows:

```bash
pip install "crawlerflow[browser]"
```

## Current foundation

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

## Development

```bash
python -m pip install -e ".[dev]"
pytest
crawlerflow validate examples/basic.yaml
crawlerflow run examples/basic.yaml
```

Run multiple workflows sequentially by supplying more paths. Execution stops at the first failed
workflow:

```bash
crawlerflow run examples/first-site.yaml examples/second-site.yaml
```

A directory argument discovers its directly contained `.yaml` and `.yml` files and runs them in
alphabetical order:

```bash
crawlerflow run examples
```

Use asynchronous mode to run every supplied workflow in parallel. All workflows are allowed to
finish; the command exits with code `1` if any workflow fails:

```bash
crawlerflow run --mode async examples/first-site.yaml examples/second-site.yaml
```

Directory discovery can also be combined with parallel execution:

```bash
crawlerflow run --mode async examples
```

Add `--progress` to display a live progress bar based on the total workflow count. The bar advances
as each workflow succeeds or fails in both sequential and asynchronous modes:

```bash
crawlerflow run --mode async --progress examples
```

Use `--concurrency` (or `-c`) to limit how many workflows run at the same time in asynchronous
mode. This avoids starting every HTTP client or browser session simultaneously:

```bash
crawlerflow run --mode async --concurrency 8 --progress examples
```

Omitting the option preserves unlimited parallel execution. `--concurrency` accepts positive
integers and can only be used with `--mode async`.

Install browser support and select Pydoll in a workflow:

```bash
python -m pip install -e ".[browser,dev]"
```

```yaml
browser:
  engine: pydoll
  headless: true
```

Browser-free workflows omit the `browser` section. Applications can also inject another
`BrowserAdapter` into `WorkflowRunner`. See `docs/http-requests.md` for direct HTTP requests and
shortened map URL coordinate resolution.

External extensions can register entry points under `crawlerflow.plugins`; workflows activate only
the plugins they list. See `docs/plugins.md` and the installable `examples/plugins/example` package
for the plugin contract, discovery command, and packaging example.
