# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-09-17

### Added

- Workflow-wide proxy support. `WorkflowContext` carries a `proxy_url` that survives parallel loop
  forks, and the built-in `http_request`, `resolve_location_url`, `enrich_html_links_http`, and
  `enrich_json_map_locations` steps route their requests through it.
- `BrowserAdapter.configure_proxy()` extension hook. Adapters that cannot proxy raise a clear
  error, keeping the contract explicit.
- Pydoll proxy support for HTTP, HTTPS, and SOCKS5 URLs, including authenticated proxies through a
  generated extension. The temporary extension directory is removed when the adapter closes.
- CapSolver reference plugin that solves captcha tasks and exposes the solution to later steps.
- Webshare reference plugin that selects proxies and publishes the active proxy to the workflow.
- Combined CapSolver and Webshare example workflows covering browser and HTTP-only Cloudflare
  challenge flows.
- Plugin documentation covering proxy-aware plugins and the new adapter hook.

### Fixed

- Disabled Jekyll processing for the published documentation site so workflow expression examples
  are no longer parsed as Liquid templates.

## [0.2.0] - 2026-08-18

### Changed

- **Breaking:** renamed the plugin contract from `CrawlerFlowPlugin` to `CrawlerflowPlugin`.
  Update imports to `from crawlerflow.plugins import CrawlerflowPlugin`.
- Normalized the project name spelling to `Crawlerflow` across code, documentation, and examples.
- Replaced domain-specific example content with generic selectors and field names. The
  `enrich_json_map_locations` step now defaults `items_path` to `item` instead of `pharmacy`.

### Added

- Published documentation site built with MkDocs Material, including a getting started guide.
- PyPI packaging metadata: license declaration, classifiers, keywords, and project URLs.
- `py.typed` marker so type checkers use the bundled annotations.
- `docs` optional dependency group and GitHub Actions workflows for docs and PyPI publishing.

## [0.1.0] - 2026-08-18

### Added

- Initial release of the declarative YAML workflow engine.
- Versioned workflow loading and validation.
- Browser-independent adapter contract with a lazy-starting Pydoll implementation.
- Extensible step registry, async executor, and event bus.
- Expression engine with variable interpolation and per-run `today` and `now` values.
- Control flow steps: `foreach`, `foreach_date`, `foreach_select`, `if`, and macros.
- Per-step retry policies, continue/fail error handling, and JSON Lines event logging.
- Plugin API with typed settings, lifecycle hooks, steps, filters, and subscribers.
- `run`, `validate`, `list-steps`, `list-plugins`, and `doctor` CLI commands.

[0.3.0]: https://github.com/mehmetemineker/crawlerflow/releases/tag/v0.3.0
[0.2.0]: https://github.com/mehmetemineker/crawlerflow/releases/tag/v0.2.0
[0.1.0]: https://github.com/mehmetemineker/crawlerflow/releases/tag/v0.1.0
