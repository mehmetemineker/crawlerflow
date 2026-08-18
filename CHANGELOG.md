# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/mehmetemineker/crawlerflow/releases/tag/v0.2.0
[0.1.0]: https://github.com/mehmetemineker/crawlerflow/releases/tag/v0.1.0
