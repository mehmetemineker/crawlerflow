# CrawlerFlow example plugin

This separately installable package demonstrates the public CrawlerFlow plugin API. It registers
the `set_output` and `decorate` workflow steps, the `surround` expression filter, and a typed
`ExampleSettings` model for YAML configuration.

From the CrawlerFlow repository root:

```bash
python -m pip install -e . -e examples/plugins/example
python -m crawlerflow list-plugins
python -m crawlerflow run examples/plugins/example/workflow.yaml
```

The workflow writes `output/plugin-result.txt` relative to its own directory.
