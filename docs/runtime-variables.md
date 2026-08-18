# Runtime variables

Crawlerflow adds two date values when each workflow run starts:

- `today`: the local calendar date.
- `now`: the local timezone-aware date and time.

Both values remain fixed for the entire run and support the built-in `date` expression filter:

```yaml
steps:
  - log:
      message: "Year={{today|date('%Y')}} month={{today|date('%m')}}"
```

Use `add_days` before `date` for relative dates. Negative values subtract days:

```yaml
steps:
  - log:
      message: "Yesterday={{today|add_days(-1)|date('%Y-%m-%d')}}"
```

## Dynamic output filenames

Dynamic values used in the final filename portion of a step `path` are made filesystem-safe
automatically. Path separators and invalid filename characters are replaced with `-`, while the
directory portion remains unchanged. For example, `MERKEZ/KÖY` becomes `MERKEZ-KÖY`:

```yaml
- save_html:
    path: "{{output_directory}}/{{loop.text}}.html"
```

A `path` consisting entirely of one expression is treated as an explicit full path and is left
unchanged.

Workflow variables named `today` or `now` override these runtime defaults when deterministic or
historical execution is needed.
