# Control flow

CrawlerFlow runs nested steps with the same registry, expression engine, validation, events, and
error handling used by top-level steps.

## Foreach

`foreach` accepts a list, tuple, or mapping. Use `as` to expose the current item under a descriptive
name inside `loop`.

```yaml
- foreach:
    items: "{{cities}}"
    as: city
    steps:
      - log:
          message: "{{loop.index}}/{{loop.length}} {{loop.city}}"
```

Available loop metadata:

- `loop.item` and `loop.<as>`: current item
- `loop.index`, `loop.index0`: one-based and zero-based indexes
- `loop.first`, `loop.last`, `loop.length`: iteration state
- `loop.key`, `loop.value`: mapping entries
- `loop.parent`: outer loop metadata in nested loops

## Parallel loops

Set `parallel: true` on `foreach`, `foreach_date`, or browser-free `foreach_select` to run their
iterations concurrently. Use the optional `concurrency` value to limit simultaneous iterations:

```yaml
- foreach_select:
    content: "{{district_page_response.body}}"
    selector: 'form[name="ara"] select[name="ilce"]'
    parallel: true
    concurrency: 8
    steps:
      - http_request:
          method: POST
          url: "{{page_url}}"
          data: "ilce={{loop.value|urlencode}}"
          save_as: district_response
      - save_html:
          path: "output/{{loop.text}}.html"
          content: "{{district_response.body}}"
```

Each iteration uses isolated loop scopes, outputs, response state, and nested step state. Changes
are merged into the parent context in declaration order after all iterations finish. Browser
adapters are not shared across parallel iterations; parallel loops therefore require a
browser-free workflow. `concurrency` requires `parallel: true` and must be positive. Omitting it
starts every iteration concurrently.

## If

A scalar condition uses normal truthiness:

```yaml
- if:
    condition: "{{enabled}}"
    then:
      - log:
          message: enabled
    else:
      - log:
          message: disabled
```

Use a structured condition for comparisons:

```yaml
- if:
    condition:
      left: "{{score}}"
      operator: gte
      right: 10
    then:
      - log:
          message: passed
```

Supported operators are `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `in`,
`not_in`, `truthy`, and `falsy`.

Nested validation happens before execution. Runtime failures report a dotted step path such as
`Step 2.1.3`.

## Date loops

`foreach_date` iterates one date or an ascending date range. The range includes every date reached
without passing `end`.

```yaml
- foreach_date:
    start: 2026-08-01
    end: 2026-08-05
    step_days: 2
    as: report_date
    steps:
      - log:
          message: "{{loop.report_date|date('%d.%m.%Y')}}"
```

`loop.iso` contains the ISO-formatted current date. Standard loop metadata remains available.

## Select loops

`foreach_select` snapshots a native HTML select element's options, selects each included value,
and executes its nested steps. CSS and XPath selectors are supported.

```yaml
- foreach_select:
    selector: "#city"
    as: city
    include_empty: false
    include_disabled: false
    exclude_values:
      - "6"
      - "34"
    steps:
      - log:
          message: "{{loop.city}}: {{loop.text}}"
```

The selected value is available as `loop.<as>` and `loop.value`. Additional metadata includes
`loop.text`, `loop.disabled`, `loop.selected`, and the zero-based `loop.option_index`.
`exclude_values` accepts one value or a list and skips matching option values. Numeric YAML values
are normalized to strings before comparison. Omitting it preserves the normal select iteration.

Use `text_overrides` to replace option text according to its value without changing the value sent
to forms or requests. The replacement is exposed as `loop.text`; the original option text remains
available as `loop.original_text`. Use `{original_text}` and `{value}` inside replacements to build
the new text from the original option. Numeric mapping keys are normalized to strings:

```yaml
- foreach_select:
    selector: 'select[name="ilce"]'
    as: district
    text_overrides:
      1: Yozgat - {original_text}
      2: Yozgat - {original_text}
      3: "Yozgat - {original_text} ({value})"
    steps:
      - save_html:
          path: "output/{{loop.text}}_{{loop.value}}.html"
```

To apply the same text template to multiple values, use grouped overrides:

```yaml
- foreach_select:
    selector: 'select[name="ilce"]'
    text_overrides:
      - values: [1536, 1537, 1518, 1540, 1524, 1525, 1997, 1526, 1528]
        text: "ZONGULDAK - {original_text}"
    steps:
      - save_html:
          path: "output/{{loop.text}}_{{loop.value}}.html"
```

For HTTP-only workflows, pass HTML through `content`. In this mode CrawlerFlow reads the options
from the supplied HTML without requiring or modifying a browser:

```yaml
- foreach_select:
    content: "{{page_response.body}}"
    selector: 'select[name="ilce"]'
    as: district
    include_empty: false
    steps:
      - log:
          message: "{{loop.value}}: {{loop.text}}"
```

Browser-free `content` mode supports tags, `#id`, `.class`, exact attribute selectors, and
descendant combinations such as `form[name="uye"] select[name="ilce"]`.

When multiple select elements match the same selector, use zero-based `match_index` to choose one.
For example, `match_index: 1` reads the second matching select:

```yaml
- foreach_select:
    content: "{{page_response.body}}"
    selector: 'select[name="ilce"]'
    match_index: 1
    steps:
      - log:
          message: "{{loop.text}}"
```

## Macros

Macros are named step sequences declared at workflow level. Pass values with `with` and access them
through `macro`.

```yaml
macros:
  search:
    - type:
        selector: "#query"
        value: "{{macro.query}}"
    - click:
        selector: "#submit"

steps:
  - run_macro:
      name: search
      with:
        query: crawlerflow
```

Macro definitions receive normal pre-run nested validation. Unknown names and direct or mutual
recursive calls fail with explicit errors.
