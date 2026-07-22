# Installation

This guide covers the full installation of `hpc-job-analyst` on an HPC
login node, including the proxy service that handles AI API calls.

## Architecture overview

```
User runs:   analyze-job /path/to/job.oXXX
                    |
                    v
         Unix socket or TCP (localhost only)
                    |
                    v
         server/proxy.py  (holds API key in environment; never on disk in cleartext)
                    |
                    v
         https://<AI API endpoint>/api/v1/chat/completions
```

The proxy must run on the same login node the user is connected to, or be
reachable via TCP from it.

## Prerequisites

- Python 3.11+
- conda (miniforge recommended) or pip
- A USAi API key (contact your HPC center's AI services team)
- systemd user services enabled on the login node (or an alternative
  process supervisor)

## Step 1 — Clone and install

```bash
git clone https://github.com/SethUnderwood/hpc-job-analyst.git
cd hpc-job-analyst

# Create the conda environment
conda env create -f environment.yml
conda activate hpc-job-analyst
```

The `analyze-job` command is now available at:

```
<conda-prefix>/envs/hpc-job-analyst/bin/analyze-job
```

Because the package is installed in editable mode, editing
`src/analyze_job/cli.py` takes effect immediately — no reinstall needed
during development.

## Step 2 — Create the API key environment file

```bash
mkdir -p ~/.config/hpc-job-analyst
touch ~/.config/hpc-job-analyst/env
chmod 600 ~/.config/hpc-job-analyst/env
```

Edit `~/.config/hpc-job-analyst/env`:

```ini
USAI_API_KEY=your-api-key-here
USAI_BASE_URL=https://api.doc.usai.gov
PROXY_PORT=8742
LOG_LEVEL=INFO
```

!!! warning "Protect this file"
    The API key must never be committed to version control or made
    world-readable.  The `chmod 600` above is mandatory.

## Step 3 — Install the proxy service

```bash
analyze-job proxy install \
  --proxy-script /path/to/hpc-job-analyst/server/proxy.py \
  --env-file ~/.config/hpc-job-analyst/env \
  --python $(which python)
```

This writes `~/.config/systemd/user/hpc-job-analyst-proxy.service` and
prints the exact commands to start and enable it.

```bash
systemctl --user daemon-reload
systemctl --user start hpc-job-analyst-proxy
systemctl --user status hpc-job-analyst-proxy
```

## Step 4 — Enable linger (survive after logout)

```bash
loginctl enable-linger $USER
```

This allows the proxy to keep running after you log out.  If the command
fails with a permission error, request linger from your HPC system
administrators.

Verify:

```bash
loginctl show-user $USER | grep Linger
# Should show: Linger=yes
```

## Step 5 — Verify

```bash
analyze-job proxy status
```

Expected output:

```
hpc-job-analyst proxy is running and reachable.
  Connected via: TCP 127.0.0.1:8742
```

## Step 6 — Optional: add a system context file

To give the AI knowledge of your site's specific systems and workflows,
place a `system-context.md` file at:

```
<install_prefix>/etc/hpc-job-analyst/system-context.md   # operator
/etc/hpc-job-analyst/system-context.md                    # system-wide admin
~/.config/hpc-job-analyst/system-context.md               # per-user
```

See the [System Context Files](system-context.md) guide and the
[example template](../examples/system-context.md.example).

## Updating

To pull the latest changes and update the environment:

```bash
cd /path/to/hpc-job-analyst
git pull
conda env update -f environment.yml --prune
```

No service restart is needed for changes to `cli.py` (editable install).
Restart the proxy if `server/proxy.py` or dependencies change:

```bash
systemctl --user restart hpc-job-analyst-proxy
```

## API key rotation

USAi API keys rotate periodically.  To update:

```bash
nano ~/.config/hpc-job-analyst/env   # update USAI_API_KEY
systemctl --user restart hpc-job-analyst-proxy
```
