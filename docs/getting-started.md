# Getting started

## Installation

```bash
pip install crawlerflow
```

Include browser support to run Pydoll-backed workflows:

```bash
pip install "crawlerflow[browser]"
```

## Your first workflow

Create `workflow.yaml`:

```yaml
version: 1

workflow:
  name: basic-example

variables:
  project: CrawlerFlow
  output_file: output/hello.txt

steps:
  - log:
      message: "Starting {{project}}"

  - save_text:
      path: "{{output_file}}"
      content: "{{project|upper}} is running."
```

Validate it, then run it:

```bash
crawlerflow validate workflow.yaml
crawlerflow run workflow.yaml
```

## CLI commands

| Command | Purpose |
| --- | --- |
| `crawlerflow run` | Execute one or more workflows |
| `crawlerflow validate` | Load and validate workflow files without executing them |
| `crawlerflow list-steps` | Print every registered step name |
| `crawlerflow list-plugins` | Print discoverable plugin entry points |
| `crawlerflow doctor` | Report environment and optional dependency status |

## Running multiple workflows

Supply more paths to run workflows sequentially. Execution stops at the first failed workflow:

```bash
crawlerflow run workflows/first-site.yaml workflows/second-site.yaml
```

A directory argument discovers its directly contained `.yaml` and `.yml` files and runs them in
alphabetical order:

```bash
crawlerflow run workflows
```

## Parallel execution

Use asynchronous mode to run every supplied workflow in parallel. All workflows are allowed to
finish; the command exits with code `1` if any workflow fails:

```bash
crawlerflow run --mode async workflows
```

Add `--progress` to display a live progress bar based on the total workflow count. The bar
advances as each workflow succeeds or fails in both sequential and asynchronous modes:

```bash
crawlerflow run --mode async --progress workflows
```

Use `--concurrency` (or `-c`) to limit how many workflows run at the same time in asynchronous
mode. This avoids starting every HTTP client or browser session simultaneously:

```bash
crawlerflow run --mode async --concurrency 8 --progress workflows
```

Omitting the option preserves unlimited parallel execution. `--concurrency` accepts positive
integers and can only be used with `--mode async`.

## Enabling a browser

Browser-free workflows omit the `browser` section entirely. Select Pydoll when a real browser is
required:

```yaml
browser:
  engine: pydoll
  headless: true
```

Applications can also inject another `BrowserAdapter` into `WorkflowRunner`. See
[HTTP requests](http-requests.md) for direct HTTP requests and shortened map URL coordinate
resolution.

## Development setup

```bash
git clone https://github.com/mehmetemineker/crawlerflow.git
cd crawlerflow
python -m pip install -e ".[browser,dev]"
pytest
ruff check .
```

Preview the documentation site locally:

```bash
python -m pip install -e ".[docs]"
mkdocs serve
```
