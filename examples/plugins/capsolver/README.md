# Crawlerflow CapSolver plugin

This separately installable plugin exposes CapSolver's generic task API to Crawlerflow. The task
object is passed through without a task-type-specific schema, so all CapSolver task types can be
used by setting the documented `task.type` and parameters. New CapSolver task types do not require
a plugin release.

Install it from the repository root:

```bash
python -m pip install -e . -e examples/plugins/capsolver
```

Set the API key outside the workflow file:

```bash
export CAPSOLVER_API_KEY="your-capsolver-api-key"
```

The complete example is available at [`workflow.yaml`](workflow.yaml). It uses an authorized
reCAPTCHA v3 test target and writes the raw CapSolver response to `output/capsolver-result.json`.

Enable the plugin in YAML:

```yaml
version: 1

workflow:
  name: capsolver-example

plugins:
  - capsolver

steps:
  - capsolver_solve:
      task:
        type: ImageToTextTask
        body: BASE64_IMAGE_DATA
      save_as: solution

  - save_json:
      path: output/capsolver-result.json
      data: "{{solution}}"
```

## Steps

| Step | API operation |
| --- | --- |
| `capsolver_create_task` | `createTask` |
| `capsolver_get_task_result` | `getTaskResult` |
| `capsolver_solve` | `createTask` plus polling `getTaskResult` |
| `capsolver_get_token` | `getToken` |
| `capsolver_get_balance` | `getBalance` |
| `capsolver_get_state` | CapSolver's documented service-state operation |
| `capsolver_request` | Any documented CapSolver API endpoint |

`capsolver_solve` handles both synchronous results, such as recognition tasks, and asynchronous
token tasks. Polling defaults to three seconds and is capped at 120 attempts, matching the
documented `getTaskResult` limit. Plugin settings can override `base_url`, `app_id`, `timeout`,
`poll_interval`, and `max_poll_attempts`; step-level `app_id` and `callback_url` take precedence.

The plugin returns CapSolver responses unchanged, including the task-specific `solution` object.
The generic `task` and `capsolver_request` payloads preserve forward compatibility with all current
and future task types and API fields. It does not inject solutions into a browser page; browser/page
integration remains the responsibility of the consuming workflow and target-site authorization.
