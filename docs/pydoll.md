# Pydoll adapter

Install the optional browser dependency:

```bash
python -m pip install -e ".[browser]"
```

Enable Pydoll in a workflow:

```yaml
browser:
  engine: pydoll
  headless: true
  start_timeout: 20
  default_wait_timeout: 10
  network_idle_period: 0.5
  download_directory: output/downloads
  arguments:
    - --window-size=1440,900
```

`binary_location` may be an absolute path or a path relative to the workflow file. The adapter
starts Chromium lazily, so a workflow with browser settings but no browser steps does not launch a
process.

## Operation mapping

| Crawlerflow operation | Pydoll API |
| --- | --- |
| `goto` | `Tab.go_to()` |
| `click`, `fill`, `wait` | `Tab.query()` and `WebElement` methods |
| `evaluate` | `Tab.execute_script()` |
| `cookies`, `set_cookies` | `Tab.get_cookies()`, `Tab.set_cookies()` |
| `browser_request` | `Tab.request.request()` |
| `download` | Browser-context `GET` request |
| `screenshot` | `Tab.take_screenshot()` |

`wait_network` subscribes to CDP request-started, loading-finished, and loading-failed events. It
returns after no tracked request remains active for `network_idle_period` seconds.

## Cookies and artifacts

```yaml
steps:
  - get_cookies:
      save_as: session_cookies
  - set_cookies:
      cookies: "{{session_cookies}}"
  - download:
      url: https://example.com/report.csv
      path: output/report.csv
      save_as: downloaded_file
  - screenshot:
      path: output/page.png
      save_as: screenshot_file
```

Relative paths resolve from the workflow file's directory. `get_cookies` also updates the shared
workflow cookie state; `download` and `screenshot` return their resolved path and optionally expose
it through `save_as`.
