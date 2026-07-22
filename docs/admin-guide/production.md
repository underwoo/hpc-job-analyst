# Production Deployment

This guide covers deploying `hpc-job-analyst` as a shared service on a
multi-user HPC login node, where the proxy runs under a dedicated service
account rather than a personal user account.

## Login node topology

The proxy must be reachable from whatever login node a user lands on.
If your site blocks inter-login-node TCP (a common configuration), you have
two options:

1. **Run a proxy on every login node** — each proxy is independent.  Users
   connect to the proxy on whichever node they land on.  This is the simplest
   approach and works with the default configuration.

2. **Open a port between login nodes** — run a single proxy on a designated
   node and configure all other nodes to forward to it.  This simplifies key
   management but requires a network change.

## System-level service

For production, run the proxy under a dedicated service account rather than
a personal user account.

**Example systemd unit (`/etc/systemd/system/hpc-job-analyst-proxy.service`):**

```ini
[Unit]
Description=hpc-job-analyst AI proxy
After=network.target

[Service]
Type=simple
User=hpc-job-analyst-svc
Group=hpc-job-analyst-svc
EnvironmentFile=/etc/hpc-job-analyst/env
ExecStart=/opt/hpc-job-analyst/bin/python /opt/hpc-job-analyst/server/proxy.py
Restart=on-failure
RestartSec=10
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

The `/etc/hpc-job-analyst/env` file should be:

```
USAI_API_KEY=<your-key>
USAI_BASE_URL=https://api.doc.usai.gov
PROXY_HOST=127.0.0.1
PROXY_PORT=8742
LOG_LEVEL=INFO
```

Permissions: `chown root:hpc-job-analyst-svc /etc/hpc-job-analyst/env`,
`chmod 640`.

## Delivering the tool to users via an environment module

```lua
-- analyze-job/0.1.0 modulefile
help([[
analyze-job — AI-assisted HPC job failure analysis tool.

Usage:
    analyze-job /path/to/job.oXXX
    analyze-job /path/to/job.oXXX --ticket
    analyze-job models
    analyze-job proxy status

Documentation: https://hpc-job-analyst.readthedocs.io
]])

whatis("AI-assisted HPC job failure analysis")

prepend_path("PATH", "/opt/hpc-job-analyst/bin")
```

## Security model

- The API key lives **only** in the proxy process's environment, sourced
  from an `EnvironmentFile` owned by the service account.
- Users have no path to read the key from `/proc` or the environment
  (standard Linux protections apply).
- All inference traffic is localhost-only; job data does not leave the node
  except via the proxy's outbound HTTPS connection to the AI API endpoint.
- The tool itself contains no credentials.

## Troubleshooting

**"Cannot reach the hpc-job-analyst proxy"**

- Check: `systemctl status hpc-job-analyst-proxy`
- Logs: `journalctl -u hpc-job-analyst-proxy -n 50`
- Verify the socket or port: `ss -tlnp | grep 8742`

**"USAI_API_KEY is not set" (in proxy logs)**

- Verify the `EnvironmentFile` path in the unit is correct.
- Verify the file contains `USAI_API_KEY=...` and is not empty.

**HTTP 429 errors**

- Rate limit hit.  Contact your AI services team for a higher limit.

**HTTP 401 errors**

- The API key has expired or rotated.  Update `USAI_API_KEY` in the
  environment file and restart the service.

**Proxy stops at user logout (user-level service)**

- Linger is not enabled.  See the [Installation guide](installation.md).
