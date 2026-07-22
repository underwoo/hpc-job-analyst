# Configuration Reference

## Configuration file locations

`analyze-job` reads client configuration from up to three files, merged in
this order (later entries override earlier ones):

| Priority | Path | Who manages it |
|----------|------|----------------|
| 1 (lowest) | `/etc/hpc-job-analyst/client.conf` | System-wide admin |
| 2 | `<install_prefix>/etc/hpc-job-analyst/client.conf` | Operator / conda env installer |
| 3 (highest) | `~/.config/hpc-job-analyst/client.conf` | Individual user |

Missing files are silently skipped.  The tool works with no config file if
the proxy is on `127.0.0.1:8742`.

A fully annotated example is available at
[`docs/examples/client.conf.example`](../examples/client.conf.example).

## Configuration options

All options live under the `[proxy]` section.

### `host`

TCP host where the proxy is listening.

- Default: `127.0.0.1`
- Example: `host = 127.0.0.1`

### `port`

TCP port where the proxy is listening.

- Default: `8742`
- Example: `port = 8742`

### `socket`

Path to a Unix domain socket.  When set and the file exists and is a socket,
it is preferred over TCP.  Leave blank or commented out for TCP-only
operation.

- Default: (empty — TCP used)
- Example: `socket = /run/user/1234/hpc-job-analyst-proxy.sock`

### `model`

AI model ID to use for analysis.  Must be a model available via the proxy's
configured API endpoint.

- Default: `claude_4_6_sonnet`
- Example: `model = claude_4_6_sonnet`

### `system_context_file`

Path to an additional Markdown context file to load, on top of the three
standard search-path files.  Useful for project- or team-specific context
without needing write access to `/etc` or the install prefix.

- Default: (empty — not used)
- Example: `system_context_file = /home/user/my-project/analysis-context.md`

## Proxy environment file

The proxy process (`server/proxy.py`) reads its configuration from an
environment file specified in the systemd unit.  This file holds the API key
and must be `chmod 600`.

| Variable | Required | Description |
|----------|----------|-------------|
| `USAI_API_KEY` | Yes | API key for the AI endpoint |
| `USAI_BASE_URL` | Yes | Base URL of the AI API (e.g. `https://api.doc.usai.gov`) |
| `PROXY_HOST` | No | Host to bind (default: `127.0.0.1`) |
| `PROXY_PORT` | No | Port to bind (default: `8742`) |
| `PROXY_SOCKET` | No | Unix socket path (optional) |
| `LOG_LEVEL` | No | Logging verbosity: `DEBUG`, `INFO`, `WARNING` (default: `INFO`) |
