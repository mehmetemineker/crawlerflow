# HTTP requests

Browser-free workflows can send requests with `http_request` and resolve shortened map links with
`resolve_location_url`.

## Resolve map coordinates

`resolve_location_url` follows HTTP redirects and extracts latitude and longitude from common
Google Maps URL formats. It checks query parameters such as `query` and `center`, `!3d...!4d...`
place coordinates, and `/@latitude,longitude` map URLs. If the final URL has no coordinates, the
same patterns are searched in the response body.

```yaml
steps:
  - resolve_location_url:
      url: https://goo.gl/maps/nHMCGfbPuUZspto36
      timeout: 30
      max_redirects: 10
      save_as: item_location

  - save_json:
      path: output/location.json
      data: "{{item_location}}"
```

The saved output has this shape:

```json
{
  "original_url": "https://goo.gl/maps/nHMCGfbPuUZspto36",
  "final_url": "https://www.google.com/maps/...",
  "status_code": 200,
  "redirect_count": 1,
  "latitude": 37.7583262,
  "longitude": 39.325349,
  "source": "final_url"
}
```

`source` is either `final_url` or `response_body`. The step is HTTP-only and does not start a
browser. It fails when redirects complete but no supported coordinate pattern is found.

Custom request headers can be supplied when needed:

```yaml
  - resolve_location_url:
      url: "{{map_url}}"
      headers:
        Accept-Language: tr-TR
      save_as: location
```

## Enrich JSON map links

`enrich_json_map_locations` resolves map URLs inside JSON objects and adds the coordinates to the
same objects. `items_path` traverses dictionaries and lists, so `item` supports both a single
`data.item` object and lists containing item objects.

```yaml
  - enrich_json_map_locations:
      data: "{{page_response.body.data}}"
      items_path: item
      url_field: map
      latitude_field: latitude
      longitude_field: longitude
      parallel: true
      concurrency: 4
      on_url_error: continue
      save_as: enriched_data

  - save_json:
      path: output/items.json
      data: "{{enriched_data}}"
```

The original `map` field is retained. Successful objects receive numeric `latitude` and
`longitude` fields. Duplicate map URLs are requested only once. Set `final_url_field` to a JSON
field name to also store the resolved URL. `on_url_error` accepts `fail` or `continue`; continued
errors are available in `json_location_enrichment_errors` workflow storage.

## Extract a JavaScript array

`extract_javascript_array` reads an assigned array from HTML or JavaScript text without starting a
browser. Nested arrays, quoted brackets, and JavaScript comments are preserved.

```yaml
  - extract_javascript_array:
      content: "{{page_response.raw_body}}"
      variable: locations
      declaration_kind: const
      save_as: locations_javascript

  - save_text:
      path: output/locations.js
      content: "{{locations_javascript}}"
```

The result is written as `const locations = [...];`. Set `declaration_kind` to `let`, `var`, or
`null` when a different declaration or only the array source is needed.
