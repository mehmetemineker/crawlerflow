# HTML output

The `save_html` step saves the active browser page when only `path` is provided:

```yaml
- save_html:
    path: output/page.html
```

Use `selectors` to save only matching elements. A single CSS selector may be written as a string:

```yaml
- save_html:
    path: output/main.html
    selectors: main
```

Multiple CSS or XPath selectors may be provided as a list:

```yaml
- save_html:
    path: output/results.html
    selectors:
      - "main .result"
      - "//section[@id='summary']"
```

Every matched element is serialized with `outerHTML`. Duplicate elements are written once, and
fragments are separated by a newline. Selector list order is preserved; matches for the first
selector are written before matches for the second selector. Document order is preserved among
matches of the same selector. If no element matches, an empty file is written.

Set `limit` to save only the first number of matched elements. For example, this saves the first
`div` found inside `body`:

```yaml
- save_html:
    path: output/first-div.html
    content: "{{request_result.body}}"
    selectors: "body div"
    limit: 1
```

Set `offset` to skip matched elements before saving. `offset: 2` starts from the third match and
can be combined with `limit`:

```yaml
- save_html:
    path: output/remaining-divs.html
    content: "{{request_result.body}}"
    selectors: "body div"
    offset: 2
```

## Supplied HTML content

Use `content` to filter or save HTML obtained from another step, such as `browser_request`:

```yaml
- save_html:
    path: output/response.html
    content: "{{request_result.body}}"
    selectors:
      - ".item-list"
      - ".pagination"
```

When `selectors` is omitted, `content` is saved unchanged. When both `content` and `selectors` are
omitted, the complete active browser HTML is saved, preserving the original behavior.

Set `ignore_comments: true` to remove HTML comments from the selected content before empty checks,
formatting, and file writing:

```yaml
- save_html:
    path: output/results.html
    content: "{{request_result.body}}"
    selectors: ".result"
    ignore_comments: true
```

When no browser adapter is configured, supplied HTML can still be filtered with CSS selectors
composed from a tag, `#id`, one or more `.class` parts, and descendant combinations. Supported
examples include `.result`, `div.result`, `section#main.result`, and
`.main-content .sixteen.columns`. Other CSS combinators, attribute selectors, pseudo-classes, and
XPath continue to use the active browser DOM implementation.

## Empty results

Set `skip_if_empty` to avoid creating a file when the final content is empty or contains only
whitespace. The check runs after selector filtering:

```yaml
- save_html:
    path: output/results.html
    content: "{{request_result.body}}"
    selectors: ".result"
    skip_if_empty: true
```

The option defaults to `false`, preserving the previous behavior. If the target file already
exists, skipping does not delete or overwrite it.

## Pretty formatting

Set `pretty` to format the final HTML with two-space indentation before writing it:

```yaml
- save_html:
    path: output/results.html
    content: "{{request_result.body}}"
    selectors: ".result"
    skip_if_empty: true
    pretty: true
```

Formatting runs after selector filtering and the empty-content check. It supports full HTML
documents and element fragments. The option defaults to `false`, so omitted formatting preserves
the original HTML text. Formatting also works for direct `http_request` output when no browser
adapter is configured; Crawlerflow uses its built-in Python formatter in that case.

## Enriching links with detail pages

Use `enrich_html_links` to find links in supplied HTML, load every linked page through the active
browser session, select detail elements, and insert them immediately after their matching link:

```yaml
- enrich_html_links:
    content: "{{list_response.body}}"
    base_url: https://example.com/list
    link_selector: 'table a[href*="/detail/"]'
    detail_selectors:
      - main .contact
      - //section[@id='hours']
    detail_wait_selector: main .contact
    timeout: 30
    delay: 0.5
    wrapper_class: linked-detail
    headers:
      Referer: https://example.com/list
    on_link_error: continue
    save_as: enriched_html
```

`link_selector` is a CSS selector for parent-page anchors. `detail_selectors` accepts one CSS or
XPath selector, or a list of them. Selected fragments are wrapped in `wrapper_tag` (default `div`)
with `wrapper_class` (default `crawlerflow-linked-content`) and a
`data-crawlerflow-source` attribute containing the detail URL.

Set `detail_wait_selector` to delay extraction until a CSS or XPath selector appears in the
returned detail HTML. Crawlerflow reloads that detail URL through the active browser session until
the selector appears or `timeout` seconds pass. The selector is optional and `timeout` defaults to
30 seconds.

Use `delay` to enforce a minimum number of seconds between detail requests. Alternatively, use
`rate_limit` to set the maximum number of requests per second:

```yaml
- enrich_html_links:
    # ...
    rate_limit: 2
```

`delay: 0.5` and `rate_limit: 2` both allow at most two requests per second. The limit also
applies when `detail_wait_selector` causes a detail URL to be requested again. Repeated URLs served
from the step cache do not consume another request. `delay` and `rate_limit` cannot be used
together; both are optional and pacing is disabled by default.

Every matching anchor receives its detail content. Repeated URLs are requested only once per step
and the cached content is inserted under each matching anchor. `on_link_error: fail` stops on the
first failed detail page; `continue` skips that URL and records the error in
`link_enrichment_errors`. Save the result by passing `{{enriched_html}}` to `save_html`.

## Browser-free HTTP enrichment

Use `enrich_html_links_http` when detail pages can be requested without a browser. The step finds
links inside each `parent_selector`, filters their URLs with `href_contains`, requests unique detail
URLs through HTTP, and inserts rendered regex values before the matching parent element closes:

```yaml
- enrich_html_links_http:
    content: "{{page_response.body}}"
    base_url: "{{page_url}}"
    parent_selector: .card
    href_contains: /detail/
    detail_regex: 'Placemark\(\[\s*(?P<latitude>-?\d+(?:\.\d+)?)\s*,\s*(?P<longitude>-?\d+(?:\.\d+)?)\s*\]'
    detail_template: '<div>Location: {latitude},{longitude}</div>'
    delay: 0.2
    wrapper_class: location-detail
    on_link_error: continue
    save_as: enriched_html
```

Captured regex groups are HTML-escaped before template rendering. `{0}` references the complete
match, numbered placeholders reference capture groups, and named placeholders reference named
groups. Repeated detail URLs are requested once and their cached content is inserted into every
matching parent. `delay` controls minimum spacing between unique HTTP requests.
