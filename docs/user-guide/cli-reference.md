# CLI Reference

## Synopsis

```
analyze-job [OPTIONS] COMMAND [ARGS]...
analyze-job LOGFILE [OPTIONS]          # shorthand — file path implies analyze
```

## Global options

| Option | Description |
|--------|-------------|
| `--help` | Show help and exit |

---

## analyze

Analyze a job stdout log file and explain any failures.

```bash
analyze-job analyze LOGFILE [OPTIONS]
analyze-job LOGFILE [OPTIONS]          # equivalent shorthand
```

**Arguments**

| Argument | Description |
|----------|-------------|
| `LOGFILE` | Path to the job stdout file (e.g. `myjob.o12345678`) |

**Options**

| Option | Default | Description |
|--------|---------|-------------|
| `--model`, `-m` | `claude_4_6_sonnet` | AI model ID to use |
| `--context`, `-c` | — | Extra context passed to the model (e.g. `"node n0447 was flaky"`) |
| `--tail` / `--no-tail` | `--tail` | When truncating large logs, prefer the end of the file |
| `--raw` | off | Print raw Markdown instead of rendered output |
| `--ticket` | off | Also generate a draft help desk ticket |
| `--socket` | from config | Unix socket path (overrides TCP when set and exists) |
| `--host` | `127.0.0.1` | Proxy TCP host |
| `--port` | `8742` | Proxy TCP port |

**Examples**

```bash
# Basic analysis
analyze-job /scratch/user/myjob.o135797758

# Add context the AI wouldn't otherwise know
analyze-job /scratch/user/myjob.o135797758 \
  --context "this is the third consecutive failure on the same nodes"

# Analysis plus a draft help desk ticket
analyze-job /scratch/user/myjob.o135797758 --ticket

# Raw Markdown, saved to a file
analyze-job /scratch/user/myjob.o135797758 --raw > analysis.md

# Use a specific model
analyze-job /scratch/user/myjob.o135797758 --model claude_4_6_sonnet
```

---

## models

List AI models available via the proxy.

```bash
analyze-job models
```

---

## proxy status

Check whether the proxy is running and reachable.

```bash
analyze-job proxy status
```

---

## proxy install

Install the proxy as a user-level systemd service.

```bash
analyze-job proxy install \
  --proxy-script PATH \
  --env-file PATH \
  [--python PATH] \
  [--host HOST] \
  [--port PORT] \
  [--socket-path PATH]
```

**Options**

| Option | Default | Description |
|--------|---------|-------------|
| `--proxy-script` | required | Path to `server/proxy.py` |
| `--env-file` | required | Path to the environment file with `USAI_API_KEY` (created if absent) |
| `--python` | system default | Absolute path to the Python interpreter to use in the service |
| `--host` | `127.0.0.1` | Host the proxy will listen on |
| `--port` | `8742` | Port the proxy will listen on |
| `--socket-path` | — | Unix socket path (optional) |
