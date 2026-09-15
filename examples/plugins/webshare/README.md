# Crawlerflow Webshare plugin

This separately installable plugin fetches the Webshare proxy list, filters it to usable entries,
selects one proxy randomly at workflow startup, and keeps that proxy for the entire workflow.

Install it from the repository root:

```bash
python -m pip install -e . -e examples/plugins/webshare
```

Set the Webshare API key outside the workflow file:

```bash
export WEBSHARE_API_KEY="your-webshare-api-key"
```

Enable the plugin:

```yaml
plugins:
  - webshare
```

The complete example is available at [`workflow.yaml`](workflow.yaml). The selected proxy is used
by the Pydoll browser and by Crawlerflow's direct HTTP steps. `webshare_proxy_info` exposes only
non-secret proxy metadata as workflow output.

For integrations such as CapSolver's Cloudflare Challenge task, set
`include_credentials: true` explicitly. The step then adds a `capsolver_proxy` URL
to the output; treat that output as secret and do not persist it in logs or files.

## Settings

```yaml
plugins:
  - name: webshare
    settings:
      mode: direct
      authentication_method: username
      country_codes: [US, DE]
      valid_only: true
      page_size: 100
      proxy_retry:
        enabled: true
        attempts_per_proxy: 3
        max_proxies: 5
        delay: 1
```

Use `mode: backbone` for Webshare backbone connections. Webshare's list API is paginated; the
plugin reads all pages before choosing a proxy. A single proxy is selected during plugin startup,
so subsequent navigation, browser requests, and direct HTTP requests in that workflow retain the
same proxy.

When `proxy_retry.enabled` is true, the CapSolver `capsolver_solve` step retries a proxy
`attempts_per_proxy` times. If the error is identified as a proxy connection error, the plugin
selects another unused proxy and repeats the attempts, up to `max_proxies` total proxies including
the first one. `delay` is applied between attempts and proxy changes. The default is disabled.

The plugin uses Webshare's documented `GET /api/v2/proxy/list/` endpoint and the proxy object's
`proxy_address`, `port`, `username`, `password`, and `valid` fields. Proxy credentials are kept in
memory and are not included by `webshare_proxy_info`.
