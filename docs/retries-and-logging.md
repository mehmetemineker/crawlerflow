# Retries and logging

## Step retry policy

Attach `retry` metadata beside any step. `attempts` includes the initial execution.

```yaml
- browser_request:
    method: POST
    url: "{{api_url}}"
  retry:
    attempts: 4
    delay: 1
    backoff: 2
    max_delay: 10
```

The first retry waits `delay` seconds. Later delays are multiplied by `backoff` and capped by
`max_delay`. Only failures raised while executing the validated step are retried; invalid
configuration and unresolved expressions fail immediately.

Steps have at-least-once execution semantics when retry is enabled. A failed attempt may already
have changed a remote system, browser page, file, output, or workflow state.

## Error policy

The default `on_error` policy is `fail`. Use `continue` to record the exhausted error and proceed to
the next step.

```yaml
- click:
    selector: "#optional-button"
  retry:
    attempts: 2
  on_error: continue
  save_error_as: click_failure
```

`save_error_as` stores a mapping in workflow outputs containing `error`, `error_type`,
`failed_step`, and `failed_path`. The same mapping is always available as `storage.last_error` to
Python integrations.

## Automatic console logging

Enable automatic step logs once at workflow level instead of adding a `log` step after every
operation:

```yaml
logging:
  console: true
```

Every top-level and nested step prints its path, name, start, completion status, and duration. A
failed step also prints its error. Retried steps report the total attempt count when they finish.

Example output:

```text
[step 1] STARTED goto
[step 1] SUCCEEDED goto (421.735 ms)
[step 2.1] STARTED browser_request
[step 2.1] SUCCEEDED browser_request (83.216 ms, 2 attempts)
```

## JSON Lines logging

Enable structured logs at workflow level:

```yaml
logging:
  console: true
  path: output/workflow.jsonl
  mode: overwrite
  include_payload: true
```

`console` and `path` can be enabled independently or together. Relative paths resolve from the
workflow file. `mode` accepts `append` or `overwrite`. Each JSONL line is an independent JSON
object with timestamp, event name, workflow, step, index, and payload.

Recorded event groups:

- Workflow started and finished, including failed status
- Step started, finished, and failed
- Retry started and finished for every retry attempt
- Browser-context and direct HTTP request started and finished

Request events do not include request/response bodies, cookies, or headers. URLs are logged as
provided, so workflows should avoid placing secrets in query strings.
