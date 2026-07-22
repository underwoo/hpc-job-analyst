# Security Audit — 2026-07-22

## Scope

Static review of the `hpc-job-analyst` working tree as of this date. Covers
the `analyze-job` CLI (`src/analyze_job/`), the local proxy server
(`server/proxy.py`), dependency manifests, configuration/secrets handling,
and repository-level tooling (CI, tests). Git history was not reviewed; no
dynamic/runtime testing was performed.

**What the tool does:** `analyze-job` is a CLI a user runs on an HPC login
node against a Slurm job's stdout log. It reads the log (and optional Slurm
job metadata via `scontrol`/`sacct`), sends it to a local proxy
(`server/proxy.py`, intended to run under systemd), which forwards the
request to an external AI API (`https://api.doc.usai.gov`) using a
server-held API key, and renders the AI's analysis (and optionally a draft
help-desk ticket) back to the user.

## Summary

| # | Finding | Severity | File(s) |
|---|---|---|---|
| 1 | No authentication on the local proxy | High | `server/proxy.py` |
| 2 | Unescaped AI output rendered through Rich markup | Medium | `src/analyze_job/cli.py` |
| 3 | Prompt-injection surface via logs/system-context files | Low/Informational | `src/analyze_job/cli.py` |
| 4 | Telemetry spool file created with ambient permissions | Low | `src/analyze_job/telemetry.py` |
| 5 | Unpinned dependencies, no CI security scanning | Low | `pyproject.toml`, `.github/` |
| 6 | Positive findings (no action needed) | Informational | various |

All findings below are documented only — no code changes were made as part
of this audit, per the scope agreed with the requester.

---

## 1. High — No authentication on the local proxy

**File:** `server/proxy.py:98-133`

The proxy exposes `GET /api/v1/models`, `POST /api/v1/chat/completions`, and
`POST /api/v1/embeddings` with no authentication, authorization, or
per-caller identity check:

```python
@app.post("/api/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body: dict = await request.json()
    ...
    resp = await _http_client.post("/api/v1/chat/completions", json=body)
    return JSONResponse(status_code=resp.status_code, content=resp.json())
```

Every request is forwarded upstream using the shared `USAI_API_KEY` held in
the proxy process's environment (`proxy.py:29`), regardless of who sent it.

**Impact:** The proxy is designed to run on shared HPC login nodes, where
many users share the same loopback network namespace and, for Unix-socket
deployments, the same filesystem. Any local process/user that can reach the
configured TCP port (default `127.0.0.1:8742`, loopback-only unless
`--host` is widened at install time via `proxy install --host`) or the Unix
socket can call `/api/v1/chat/completions` with an arbitrary `model`,
`messages`, `temperature`, and `max_tokens` payload. This amounts to an
unauthenticated, credentialed relay to the upstream AI API for anyone
co-resident on the node: they can consume another user's proxy instance's
API quota/budget with no allowlisting, rate limiting, or audit trail beyond
client-side telemetry (which the caller can disable with `--no-telemetry`,
since telemetry is opt-out and written by the *client*, not the proxy).

**Recommendation:** Authenticate callers before forwarding — e.g. restrict
the Unix socket via filesystem permissions and/or verify the peer's UID via
`SO_PEERCRED` (so only the owning user's client can call it), or require a
shared local token generated at `proxy install` time and checked on each
request. This is a deployment-model decision (per-user proxy instance vs.
shared instance, socket vs. TCP) and is intentionally left for the
maintainer to decide rather than implemented as part of this audit.

---

## 2. Medium — Unescaped AI-generated text rendered through Rich's markup parser

**File:** `src/analyze_job/cli.py:805-813` (contrast with the correctly
escaped case at line 799-804)

```python
console.print(Panel(
    Markdown(result),              # line 800 — safe: wrapped in Markdown()
    ...
))
if ticket:
    console.print(Panel(
        ticket_text,                # line 808 — unsafe: raw string
        title="[bold yellow]Suggested Help Desk Ticket[/bold yellow]",
        ...
    ))
```

`rich.console.Console` has `markup=True` by default, so a bare string
passed to `Panel()` (or printed directly) is parsed for `[...]`-bracketed
Rich console markup, including style tags and `[link=...]` hyperlink
markup. `ticket_text` is LLM-generated content derived from the job stdout
log — content an attacker can influence, since a Slurm job's stdout can
contain arbitrary program output. If that content steers the model into
emitting bracketed markup (a classic prompt-injection outcome), it will be
interpreted by the terminal rather than displayed literally — e.g. hidden
or spoofed styling, or a clickable hyperlink that doesn't go where the
visible text suggests.

**Recommendation:** Render `ticket_text` the same safe way `result`
already is — e.g. `rich.markup.escape(ticket_text)` before passing to
`Panel()`, or wrap it in `rich.text.Text(ticket_text)` (which does not
interpret markup) instead of a bare string.

---

## 3. Low/Informational — Prompt-injection surface via job logs and tiered system-context files

**File:** `src/analyze_job/cli.py:130-256`, `cli.py:540-543`

The job stdout log and up to four tiers of `system-context.md` files
(installer/operator, system-wide admin, per-user, and an explicit
`client.conf` override) are concatenated verbatim into the LLM system/user
prompt with no sanitization (`_load_system_context`, `_analyze`). This is
inherent to the tool's purpose (analyzing arbitrary log content) and its
impact today is limited to misleading analysis or ticket text — there is no
code-execution path from this content. However, the install-prefix tier
(`<install_prefix>/etc/hpc-job-analyst/system-context.md`) is loaded and
trusted for *every* user of the tool on that system, so a write
vulnerability in that directory's permissions would allow one user (or a
compromised install process) to inject instructions into every other
user's analysis session.

**Recommendation:** Confirm and document that `<install_prefix>/etc/` is
writable only by the installer/admin account in production deployments
(this is a deployment/ops concern, not a code defect), and consider noting
the trust model for context-file tiers in `docs/admin-guide/`.

---

## 4. Low — Telemetry spool file created without explicit permissions

**File:** `src/analyze_job/telemetry.py:119-125`

```python
spool = _spool_file(install_prefix)
spool.parent.mkdir(parents=True, exist_ok=True)
with spool.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(record, default=str) + "\n")
```

Directory and file creation rely entirely on the invoking process's ambient
umask; no explicit `chmod`/`os.umask` call constrains permissions. The
records written include `username`, `hostname`, `cluster`, and `partition`
(`telemetry.py:13-31`). On a shared filesystem, depending on the
install-prefix directory's ACLs and each user's umask, this per-node JSONL
spool file could end up world-readable, exposing usage metadata across
users. This is a smaller-scope version of the same class of issue the
project already mitigates elsewhere: `proxy_install` explicitly
`chmod 0600`s the generated API-key environment file
(`cli.py:919-937`).

**Recommendation:** Apply the same explicit-permissions discipline here —
e.g. `os.umask(0o077)` around creation, or an explicit `os.chmod()` call on
the spool file/directory after creation.

---

## 5. Low — Unpinned dependencies, no CI security scanning

**Files:** `pyproject.toml:30-36`, `.github/`

```toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "httpx>=0.27.0",
    "click>=8.1.7",
    "rich>=13.7.0",
]
```

All dependencies use open-ended `>=` bounds with no lockfile
(`requirements.txt`, `poetry.lock`, `uv.lock`, etc.), so installed versions
float and builds are not reproducible. There is also no CI at all —
`.github/` contains only `ISSUE_TEMPLATE/` and `PULL_REQUEST_TEMPLATE.md`,
no `workflows/` directory — so there is no automated dependency
vulnerability scanning (`pip-audit`/`safety`), linting, or test execution
on this repository. The PR template's manual checklist item ("No secrets,
API keys, or site-specific paths committed") is a process control, not
automated tooling.

**Recommendation:** Add a lockfile (or otherwise pin dependency versions)
and a CI workflow that runs `pip-audit` (or `safety`) against the resolved
environment on each push/PR.

---

## 6. Informational — Positive findings

Noted for a balanced picture of the codebase's baseline security posture:

- **Subprocess calls are injection-safe.** `_run_cmd`/`_slurm_job_info`
  (`cli.py:416-473`) invoke `scontrol`/`sacct` via `subprocess.Popen` with
  list-form arguments (no `shell=True`), and the `job_id` passed in is
  constrained by regex to digits only (`_extract_job_id`, `cli.py:405-413`).
  No shell/argument injection path was found.
- **No committed secrets.** `.gitignore` explicitly excludes `etc/`,
  `*.env`, `.env`, `env.d/`, and `var/` (telemetry spool) with a "never
  commit these" comment, and this is honored — no keys, tokens, `.pem`
  files, or credentials are present in the working tree.
- **API key handling.** `USAI_API_KEY` is documented to live only in a
  systemd `EnvironmentFile`, never in client-facing config, and
  `proxy install` `chmod 0600`s the generated stub env file
  (`cli.py:919-937`).
- **No unsafe deserialization or dynamic execution.** No `eval`, `exec`,
  `os.system`, `pickle`, `marshal`, or `yaml.load` usage was found
  anywhere in the codebase; all parsing is standard `json`.

---

## Gaps in audit coverage

There is no `tests/` directory anywhere in this repository, so none of the
findings above have regression coverage. Anyone implementing fixes for
these findings should also consider adding tests (e.g. a test asserting
the proxy rejects unauthenticated requests once auth is added, or a test
asserting `ticket_text` is escaped before rendering) so the fixes don't
silently regress.
