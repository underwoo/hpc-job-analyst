#!/usr/bin/env python3
"""
analyze-job — AI-assisted HPC job failure analysis tool.

Usage:
    analyze-job <stdout_file> [options]

The tool sends the job log to the hpc-job-analyst proxy running on the local login node
and prints a structured analysis of any failures.

Dependencies:
    pip install httpx click rich
"""

import os
import sys
import re
import ast as _ast
import configparser
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Optional

import httpx
import click
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

from analyze_job import __version__
from analyze_job.telemetry import write_record as _telemetry_write

# ---------------------------------------------------------------------------
# Configuration file resolution
#
# Search order (later entries override earlier ones):
#   1. /etc/hpc-job-analyst/client.conf          — system-wide
#   2. <install_prefix>/etc/hpc-job-analyst/client.conf  — operator / conda env
#   3. ~/.config/hpc-job-analyst/client.conf     — per-user override
#
# The install prefix is derived from the location of this file:
#   <prefix>/lib/python*/site-packages/analyze_job/cli.py  -> <prefix>
# or for an editable install the repo root is two levels above src/:
#   <repo>/src/analyze_job/cli.py  -> <repo>  (prefix = repo root)
# ---------------------------------------------------------------------------

def _install_prefix() -> Path:
    """Return the install prefix by walking up from this file."""
    here = Path(__file__).resolve()
    # Walk upward looking for a bin/ sibling that contains analyze-job,
    # which is the reliable indicator of the actual prefix.
    # Depth needed by install layout:
    #   editable (src layout):  src/analyze_job/cli.py  -> 3 levels up = repo root
    #   venv non-editable:      lib/pythonX.Y/site-packages/analyze_job/cli.py
    #                           -> 5 levels up = venv root
    p = here
    for _ in range(6):
        p = p.parent
        if (p / "bin" / "analyze-job").exists():
            return p
    # Fallback: two levels above src/analyze_job/
    return here.parent.parent.parent


def _config_search_paths() -> list:
    prefix = _install_prefix()
    return [
        Path("/etc/hpc-job-analyst/client.conf"),
        prefix / "etc" / "hpc-job-analyst" / "client.conf",
        Path.home() / ".config" / "hpc-job-analyst" / "client.conf",
    ]


def _load_client_config() -> configparser.ConfigParser:
    """
    Read all config files that exist (in priority order) and return a merged
    ConfigParser.  Missing files are silently skipped.
    """
    cfg = configparser.ConfigParser()
    # Built-in defaults — used when no config file is found at all
    cfg.read_dict({"proxy": {
        "host":   "127.0.0.1",
        "port":   "8742",
        "socket": "",
    }})
    existing = [str(p) for p in _config_search_paths() if p.exists()]
    cfg.read(existing)
    return cfg


# Load once at import time; commands read from this object
_CLIENT_CFG = _load_client_config()

_DEFAULT_SOCKET    = _CLIENT_CFG.get("proxy", "socket",    fallback="")
_DEFAULT_HOST      = _CLIENT_CFG.get("proxy", "host",      fallback="127.0.0.1")
_DEFAULT_PORT      = int(_CLIENT_CFG.get("proxy", "port",  fallback="8742"))
_DEFAULT_MODEL     = _CLIENT_CFG.get("proxy", "model",     fallback="claude_4_6_sonnet")
_DEFAULT_TELEMETRY = _CLIENT_CFG.getboolean("proxy", "telemetry", fallback=True)

# Maximum characters of log to send (avoids exceeding context limits)
_MAX_LOG_CHARS = 80_000

console = Console()

# ---------------------------------------------------------------------------
# System context file resolution
#
# All context files that exist are loaded and concatenated in this order:
#   1. <install_prefix>/etc/hpc-job-analyst/system-context.md  — installer/operator
#   2. /etc/hpc-job-analyst/system-context.md                  — system-wide admin
#   3. ~/.config/hpc-job-analyst/system-context.md             — per-user
#
# An explicit path may also be set via system_context_file in client.conf
# [proxy] section, which is appended after the above three if it resolves to
# a different path from all of them.
# ---------------------------------------------------------------------------

def _system_context_search_paths() -> list:
    prefix = _install_prefix()
    return [
        prefix / "etc" / "hpc-job-analyst" / "system-context.md",
        Path("/etc/hpc-job-analyst/system-context.md"),
        Path.home() / ".config" / "hpc-job-analyst" / "system-context.md",
    ]


def _load_system_context() -> str:
    """
    Load and concatenate all system-context.md files that exist, in priority
    order (installer -> sysadmin -> user).  Each section is prefixed with a
    header identifying its source so the model can distinguish them.

    An explicit path in client.conf [proxy] system_context_file is appended
    last (after the three standard locations) if it resolves to a path not
    already loaded.
    """
    sections = []
    loaded_paths = set()

    source_labels = [
        "Installer/operator context",
        "System-wide administrator context",
        "User context",
    ]

    for path, label in zip(_system_context_search_paths(), source_labels):
        resolved = path.resolve() if path.exists() else None
        if resolved and resolved not in loaded_paths:
            try:
                text = path.read_text(errors="replace").strip()
                if text:
                    sections.append(f"<!-- {label}: {path} -->\n{text}")
                    loaded_paths.add(resolved)
            except OSError:
                pass

    # Explicit override from client.conf
    explicit = _CLIENT_CFG.get("proxy", "system_context_file", fallback="").strip()
    if explicit:
        explicit_path = Path(explicit).expanduser().resolve()
        if explicit_path.exists() and explicit_path not in loaded_paths:
            try:
                text = explicit_path.read_text(errors="replace").strip()
                if text:
                    sections.append(
                        f"<!-- Additional context (system_context_file): {explicit_path} -->\n{text}"
                    )
            except OSError:
                pass

    if not sections:
        return ""

    return (
        "=== SYSTEM AND WORKFLOW CONTEXT ===\n"
        "The following site-specific and workflow-specific information has been\n"
        "provided to help you give accurate, workflow-aware analysis and suggestions.\n"
        "Apply the guidance in each section only when it is relevant to the job\n"
        "being analyzed.  Do not apply guidance from one workflow to a different\n"
        "workflow.\n\n"
        + "\n\n---\n\n".join(sections)
        + "\n=== END SYSTEM AND WORKFLOW CONTEXT ==="
    )


# ---------------------------------------------------------------------------
# System prompt for the analysis
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT_BASE = """\
You are an expert in HPC (High Performance Computing) job analysis. You
specialize in diagnosing failures in parallel scientific workloads running
under SLURM on large-scale HPC systems, including Cray systems using
cray-mpich over high-speed fabrics such as Slingshot/OFI.

When given a job stdout log, you will:
1. Identify the root cause of the failure clearly and concisely.
2. Distinguish between hardware/infrastructure failures (node failures, network
   fabric errors, filesystem issues) and software/model failures (bad namelist,
   missing input file, model physics crash, etc.).
3. Report the exact error messages that indicate the failure.
4. State which segment number and simulation date the failure occurred at,
   if applicable.
5. Recommend concrete next steps appropriate for a regular user — see guidance
   below on what users can and cannot do.
6. If the job completed successfully, say so.

User permissions and recommended actions:
- Users DO NOT have SLURM administrator privileges. Never suggest commands that
  require elevated access, such as scontrol, squeue -u other users, draining
  nodes, changing node state, or any other SLURM admin operations.
- For transient infrastructure failures (e.g. network fabric errors, OFI retry
  exceeded, node communication timeouts, brief filesystem errors): the primary
  recommendation is always to simply resubmit the job. Advise the user to
  report the issue to the system administrators only if the failure repeats on
  the next attempt.
- For persistent or deterministic failures (e.g. missing input file, bad
  namelist value, model code error, quota exceeded): recommend the specific
  user-actionable fix (edit the namelist, stage the missing file, etc.).
- If a specific compute node appears suspect, users may optionally use the
  SLURM --exclude flag in their job submission to avoid it, but this is
  secondary to simply resubmitting first.
- Only suggest workflow-specific recovery steps (such as checking restart
  files, re-staging inputs, or re-running a particular stage) if the
  SYSTEM AND WORKFLOW CONTEXT section above explicitly describes that step
  as appropriate for the detected workflow.  Do not invent workflow-specific
  recovery procedures that are not mentioned in that context.

Format your response in Markdown with clear section headers.
Keep your response focused and actionable — avoid unnecessary background.

At the very end of your response, on its own line, add exactly one
machine-readable classification tag in this format (choose the single best
match):

<!-- CLASSIFICATION: infrastructure -->
<!-- CLASSIFICATION: model -->
<!-- CLASSIFICATION: success -->
<!-- CLASSIFICATION: unknown -->

Use "infrastructure" for hardware, network fabric, node, or filesystem failures.
Use "model" for software/science failures (bad namelist, missing input, model crash).
Use "success" if the job completed without error.
Use "unknown" if the failure type cannot be determined from the log.
Include exactly one such tag.  It is stripped from the displayed output automatically.
"""


def _build_system_prompt() -> str:
    """Assemble the full system prompt, injecting site context if available."""
    context = _load_system_context()
    if context:
        return _SYSTEM_PROMPT_BASE + "\n" + context + "\n"
    return _SYSTEM_PROMPT_BASE


# ---------------------------------------------------------------------------
# Classification tag extraction
# ---------------------------------------------------------------------------
_CLASSIFICATION_RE = re.compile(
    r'<!--\s*CLASSIFICATION:\s*(infrastructure|model|success|unknown)\s*-->\s*$',
    re.IGNORECASE | re.MULTILINE,
)


def _extract_classification(text: str) -> tuple:
    """
    Find and remove the CLASSIFICATION tag from the AI response.
    Returns (classification_value_or_None, cleaned_text).
    """
    m = _CLASSIFICATION_RE.search(text)
    if m:
        value = m.group(1).lower()
        cleaned = _CLASSIFICATION_RE.sub("", text).rstrip()
        return value, cleaned
    return None, text


# ---------------------------------------------------------------------------
# Helper: build HTTP client that speaks to the proxy
# ---------------------------------------------------------------------------
def _make_client(socket_path: str, host: str, port: int) -> httpx.Client:
    """Return an httpx.Client connected to the proxy via Unix socket or TCP.

    Unix socket is preferred when socket_path is set and the file exists.
    TCP is used otherwise (the default for most users).
    """
    sock = Path(socket_path) if socket_path else None
    if sock and sock.exists() and sock.is_socket():
        transport = httpx.HTTPTransport(uds=socket_path)
        return httpx.Client(transport=transport, base_url="http://localhost",
                            timeout=httpx.Timeout(connect=5.0, read=300.0,
                                                  write=30.0, pool=5.0))
    return httpx.Client(base_url=f"http://{host}:{port}",
                        timeout=httpx.Timeout(connect=5.0, read=300.0,
                                              write=30.0, pool=5.0))


def _check_proxy(client: httpx.Client) -> bool:
    try:
        r = client.get("/health")
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helper: read and trim the log file
# ---------------------------------------------------------------------------
def _read_log(path: Path, tail: bool) -> str:
    try:
        text = path.read_text(errors="replace")
    except PermissionError:
        raise click.ClickException(
            f"Permission denied: cannot read {path}\n"
            f"Check that you have read access to this file."
        )
    except FileNotFoundError:
        raise click.ClickException(
            f"File not found: {path}"
        )
    except OSError as exc:
        raise click.ClickException(
            f"Cannot read {path}: {exc.strerror}"
        )
    if len(text) <= _MAX_LOG_CHARS:
        return text
    if tail:
        # Keep the end of the file — errors are usually at the bottom
        trimmed = text[-_MAX_LOG_CHARS:]
        return f"[... log truncated, showing last {_MAX_LOG_CHARS} characters ...]\n\n" + trimmed
    else:
        # Keep both ends — header has setup info, tail has the crash
        half = _MAX_LOG_CHARS // 2
        return (text[:half]
                + f"\n\n[... {len(text) - _MAX_LOG_CHARS} characters omitted ...]\n\n"
                + text[-half:])


# ---------------------------------------------------------------------------
# Ticket prompt
# ---------------------------------------------------------------------------
_TICKET_PROMPT = """\
Based on the job analysis above, and the job metadata provided, write a \
help desk ticket to submit to the system administrators.

Rules:
- State facts only. Do not speculate about what the user has tried, will \
try, or plans to do.
- Do NOT include any mention of whether the failure is a first occurrence, \
has been seen before, or is repeatable — unless the user explicitly stated \
that in the Additional context field of the analysis.  Omit any such \
statement entirely rather than saying it is unknown.
- Do not include any commands that require admin privileges.
- Do not repeat the full error log — include only the key error message(s).
- Write in third person (e.g. "The job failed..." not "I submitted...").
- The subject line must include the cluster name and a brief description.
- Always include the full Slurm job ID in copy-pasteable form so admins \
can run their own sacct queries.
- Always include the job start and end times so admins can correlate \
with system logs.
- Always include the complete allocated node list exactly as reported \
by Slurm (do not summarise or truncate it).
- Quote the exact error string(s) verbatim from the log; do not paraphrase.

Format your response as exactly two labeled sections:

Subject: <cluster>: <one-line summary, max 80 characters total>

Body:
<Paragraph 1 — Job identification: job name, Slurm job ID, cluster, \
partition, workflow system, stdout file path, job start time, and job \
end time.>
<Paragraph 2 — Error summary: the exact error message(s) verbatim, the \
segment number and simulation date if applicable, and which nodes were \
implicated in the error.>
<Paragraph 3 — Node/infrastructure details: the complete allocated node \
list as reported by Slurm, and any specific nodes directly implicated \
in the failure.>
<Paragraph 4 — Request: a factual statement of what is being reported and \
a request for the admins to investigate the underlying infrastructure issue.>
"""

# Reminder appended below the rendered ticket panel
_TICKET_USER_REMINDER = (
    "[dim]Review the ticket above before submitting.  "
    "Add any additional context that may help the administrators "
    "(e.g. whether this failure has occurred before, steps already taken, "
    "or other relevant observations).[/dim]"
)

# AI disclaimer printed at the end of all output
_AI_DISCLAIMER = (
    "[dim italic]Note: This report was generated by an AI assistant. "
    "The information provided may be incomplete or incorrect. "
    "Please verify all findings and suggestions before taking action.[/dim italic]"
)


# ---------------------------------------------------------------------------
# Job metadata helpers
# ---------------------------------------------------------------------------
def _extract_job_id(logfile: Path) -> Optional[str]:
    """Parse job ID from filename patterns like name.oJOBID or name_JOBID."""
    m = re.search(r'\.o(\d+)$', logfile.name)
    if m:
        return m.group(1)
    m = re.search(r'_(\d{6,})$', logfile.name)
    if m:
        return m.group(1)
    return None


def _run_cmd(*args) -> str:
    """Run a shell command, return stdout+stderr combined, never raises."""
    try:
        p = subprocess.Popen(list(args),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(timeout=15)
        return (out + err).decode(errors="replace").strip()
    except Exception as exc:
        return f"(command failed: {exc})"


def _slurm_job_info(job_id: str) -> dict:
    """
    Fetch job metadata from SLURM.  Try scontrol first (job still in queue
    or recently finished), fall back to sacct (historical).

    Returns a dict with keys: cluster, partition, nodes, node_list,
    job_name, state, start, end  — any unknown field is "unknown".
    """
    info = {k: "unknown" for k in
            ("cluster", "partition", "nodes", "node_list",
             "job_name", "state", "start", "end")}

    # --- scontrol show job ---
    raw = _run_cmd("scontrol", "show", "job", job_id)
    if "Invalid job id" not in raw and "command failed" not in raw:
        for key, pattern in [
            ("cluster",   r"ClusterName=(\S+)"),
            ("partition", r"Partition=(\S+)"),
            ("nodes",     r"NumNodes=(\S+)"),
            ("node_list", r"NodeList=(\S+)"),
            ("job_name",  r"JobName=(\S+)"),
            ("state",     r"JobState=(\S+)"),
            ("start",     r"StartTime=(\S+)"),
            ("end",       r"EndTime=(\S+)"),
        ]:
            m = re.search(pattern, raw)
            if m and m.group(1) not in ("None", "N/A", "Unknown"):
                info[key] = m.group(1)
        return info

    # --- sacct fallback ---
    raw = _run_cmd(
        "sacct", "-j", job_id, "--noheader", "--parsable2",
        "--format=JobName,Cluster,Partition,NNodes,NodeList,State,Start,End",
    )
    if raw and "command failed" not in raw:
        lines = [l for l in raw.splitlines() if not l.startswith("batch|")
                 and not l.startswith("extern|")]
        if lines:
            parts = lines[0].split("|")
            keys = ("job_name", "cluster", "partition", "nodes",
                    "node_list", "state", "start", "end")
            for k, v in zip(keys, parts):
                if v and v not in ("None", "N/A", "Unknown"):
                    info[k] = v

    return info


# Workflow detection patterns applied to the first ~200 lines of the log
_WORKFLOW_PATTERNS = [
    (r"FRE RUNSCRIPT|freCommandsVersion|bronx-\d+|rtsxml\s*=",
     "FRE (Flexible Runtime Environment)"),
    (r"rocotorun|rocotostat|<workflow>|<cycledef>",
     "Rocoto"),
    (r"ecflow_client|ecflow_server|--port.*ecflow|ecflow suite",
     "ecFlow"),
    (r"community.global.workflow|global-workflow|gfs\.v\d|GEFS",
     "Community Global Workflow"),
    (r"unified.workflow|uwtools|uwconfig",
     "Unified Workflow"),
]


def _detect_workflow(log_text: str) -> str:
    # Search only the first 200 lines for speed
    head = "\n".join(log_text.splitlines()[:200])
    for pattern, name in _WORKFLOW_PATTERNS:
        if re.search(pattern, head, re.IGNORECASE):
            return name
    return "could not be determined"


def _build_job_metadata(logfile: Path, log_text: str) -> str:
    """
    Collect all available job metadata and return a formatted block
    to inject into the ticket prompt.
    """
    job_id   = _extract_job_id(logfile)
    workflow = _detect_workflow(log_text)
    slurm    = _slurm_job_info(job_id) if job_id else \
               {k: "unknown" for k in
                ("cluster", "partition", "nodes", "node_list",
                 "job_name", "state", "start", "end")}

    lines = [
        "=== JOB METADATA ===",
        f"Stdout file:  {logfile.resolve()}",
        f"Job ID:       {job_id or 'could not be determined'}",
        f"Job name:     {slurm['job_name']}",
        f"Cluster:      {slurm['cluster']}",
        f"Partition:    {slurm['partition']}",
        f"Nodes used:   {slurm['nodes']}",
        f"Node list:    {slurm['node_list']}",
        f"Job state:    {slurm['state']}",
        f"Start time:   {slurm['start']}",
        f"End time:     {slurm['end']}",
        f"Workflow:     {workflow}",
        "=== END METADATA ===",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main analysis call
# ---------------------------------------------------------------------------
def _analyze(client: httpx.Client, log_text: str, model: str,
             extra_context: Optional[str]) -> tuple:
    """
    Run the analysis API call.
    Returns (cleaned_analysis_text, classification_or_None, latency_ms).
    """
    system_prompt = _build_system_prompt()
    user_content = "Please analyze the following HPC job stdout log and explain why the job failed (or confirm it succeeded).\n\n"
    if extra_context:
        user_content += f"Additional context from the user: {extra_context}\n\n"
    user_content += f"=== JOB STDOUT LOG ===\n{log_text}\n=== END LOG ==="

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }

    t0 = time.monotonic()
    response = client.post("/api/v1/chat/completions", json=payload)
    response.raise_for_status()
    latency_ms = int((time.monotonic() - t0) * 1000)
    data = response.json()

    try:
        raw = data["choices"][0]["message"]["content"]
        classification, cleaned = _extract_classification(raw)
        return cleaned, classification, latency_ms
    except (KeyError, IndexError) as exc:
        raise click.ClickException(
            f"Unexpected response structure from proxy: {data}"
        ) from exc


def _generate_ticket(client: httpx.Client, analysis: str,
                     metadata: str, model: str) -> tuple:
    """
    Generate the help desk ticket.
    Returns (ticket_text, latency_ms).
    """
    system_prompt = _build_system_prompt()
    user_content = (
        f"{metadata}\n\n"
        f"Using the analysis above and the job metadata, "
        f"write the help desk ticket as instructed.\n\n"
        f"{_TICKET_PROMPT}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system",    "content": system_prompt},
            {"role": "assistant", "content": analysis},
            {"role": "user",      "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }

    t0 = time.monotonic()
    response = client.post("/api/v1/chat/completions", json=payload)
    response.raise_for_status()
    latency_ms = int((time.monotonic() - t0) * 1000)
    data = response.json()

    try:
        return data["choices"][0]["message"]["content"], latency_ms
    except (KeyError, IndexError) as exc:
        raise click.ClickException(
            f"Unexpected response structure from proxy: {data}"
        ) from exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class _FileFallbackGroup(click.Group):
    """A click Group that falls back to the 'analyze' command when the first
    argument is an existing file path rather than a known subcommand name."""

    def parse_args(self, ctx, args):
        # If the first non-option argument is not a known command name but
        # is a path that exists on disk, prepend 'analyze' so the user can
        # write:  analyze-job /path/to/job.oXXX  [options]
        if args and not args[0].startswith("-"):
            if args[0] not in self.commands:
                try:
                    path_exists = Path(args[0]).exists()
                except PermissionError:
                    # Can't stat the path — treat it as a file argument anyway
                    # so the analyze command can emit a proper error message.
                    path_exists = True
                except OSError:
                    path_exists = False
                if path_exists:
                    args = ["analyze"] + list(args)
        return super().parse_args(ctx, args)


@click.group(cls=_FileFallbackGroup)
@click.version_option(version=__version__, prog_name="analyze-job")
def cli():
    """AI-assisted HPC job failure analysis tool.

    You can invoke analysis directly by passing a log file path:

    \b
        analyze-job /path/to/job.o135797758
        analyze-job analyze /path/to/job.o135797758
    """
    pass


@cli.command("analyze")
@click.argument("logfile", type=click.Path(path_type=Path))
@click.option("--model", "-m", default=_DEFAULT_MODEL, show_default=True,
              help="AI model ID to use for analysis.")
@click.option("--context", "-c", default=None,
              help="Optional extra context to pass to the model (e.g. 'this node has crashed before').")
@click.option("--tail/--no-tail", default=True, show_default=True,
              help="When truncating large logs, prefer the tail (end) of the file.")
@click.option("--raw", is_flag=True, default=False,
              help="Print raw Markdown instead of rendered output.")
@click.option("--ticket", is_flag=True, default=False,
              help="Also generate a suggested help desk ticket subject and body.")
@click.option("--no-telemetry", "telemetry_off", is_flag=True, default=False,
              help="Disable usage telemetry for this invocation.")
@click.option("--socket", "socket_path", default=_DEFAULT_SOCKET,
              help="Unix socket path (optional; overrides TCP if set and exists).")
@click.option("--host", default=_DEFAULT_HOST, show_default=True,
              help="Proxy TCP host.")
@click.option("--port", default=_DEFAULT_PORT, show_default=True,
              help="Proxy TCP port.")
def analyze_cmd(logfile, model, context, tail, raw, ticket, telemetry_off,
                socket_path, host, port):
    """Analyze a job stdout log file and explain any failures.

    LOGFILE is the path to the job stdout file (e.g. myjob.o12345678).
    Pass --ticket to also generate a suggested help desk ticket.
    """
    use_telemetry = _DEFAULT_TELEMETRY and not telemetry_off

    # Telemetry accumulators — populated as the invocation progresses
    _t_error:            Optional[str] = None
    _t_classification:   Optional[str] = None
    _t_cluster:          str           = "unknown"
    _t_partition:        str           = "unknown"
    _t_workflow:         Optional[str] = None
    _t_latency_analysis: int           = 0
    _t_latency_ticket:   Optional[int] = None
    _t_proxy_ok:         bool          = False
    _t_log_size:         int           = 0
    _t_log_truncated:    bool          = False
    _t_ctx_count:        int           = 0

    with _make_client(socket_path, host, port) as client:
        if not _check_proxy(client):
            cfg_paths = "\n".join(f"    {p}" for p in _config_search_paths())
            if use_telemetry:
                _telemetry_write(
                    _install_prefix(),
                    tool_version=__version__, model=model,
                    workflow_detected=None, cluster="unknown", partition="unknown",
                    failure_type=None, log_size_chars=0, log_truncated=False,
                    flags={"ticket": ticket, "raw": raw,
                           "context_supplied": bool(context)},
                    context_files_count=0, latency_analysis_ms=0,
                    latency_ticket_ms=None, proxy_reachable=False,
                    error="ProxyUnreachable",
                )
            raise click.ClickException(
                f"Cannot reach the hpc-job-analyst proxy.\n"
                f"  Tried TCP: {host}:{port}\n"
                + (f"  Tried Unix socket: {socket_path}\n" if socket_path else "")
                + f"\nConfiguration files searched (in priority order):\n{cfg_paths}\n\n"
                f"Check the proxy is running: analyze-job proxy status\n"
            )

        _t_proxy_ok = True

        console.print(f"[dim]Reading log file:[/dim] {logfile}")
        log_text = _read_log(logfile, tail)
        _t_log_size      = len(log_text)
        _t_log_truncated = len(log_text) >= _MAX_LOG_CHARS
        _t_workflow      = _detect_workflow(log_text)
        console.print(f"[dim]Log size:[/dim] {len(log_text):,} chars  "
                      f"[dim]Model:[/dim] {model}")

        # Report which system context files were loaded (if any)
        ctx_paths = [p for p in _system_context_search_paths() if p.exists()]
        explicit_ctx = _CLIENT_CFG.get("proxy", "system_context_file", fallback="").strip()
        if explicit_ctx and Path(explicit_ctx).expanduser().exists():
            ctx_paths.append(Path(explicit_ctx).expanduser())
        _t_ctx_count = len(ctx_paths)
        if ctx_paths:
            console.print("[dim]System context loaded from:[/dim]")
            for p in ctx_paths:
                console.print(f"[dim]  {p}[/dim]")

        # Collect SLURM metadata before the spinner (may invoke subprocesses)
        metadata: Optional[str] = None
        if ticket:
            console.print("[dim]Collecting job metadata from SLURM...[/dim]")
            metadata = _build_job_metadata(logfile, log_text)
            # Extract cluster/partition for telemetry from the metadata block
            _m = re.search(r"Cluster:\s+(\S+)", metadata)
            if _m and _m.group(1) != "unknown":
                _t_cluster = _m.group(1)
            _m = re.search(r"Partition:\s+(\S+)", metadata)
            if _m and _m.group(1) != "unknown":
                _t_partition = _m.group(1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Analyzing with AI...", total=None)
            result, _t_classification, _t_latency_analysis = \
                _analyze(client, log_text, model, context)
            if ticket:
                progress.update(task, description="Generating help desk ticket...")
                ticket_text, _t_latency_ticket = \
                    _generate_ticket(client, result, metadata or "", model)

    if use_telemetry:
        _telemetry_write(
            _install_prefix(),
            tool_version=__version__,
            model=model,
            workflow_detected=_t_workflow,
            cluster=_t_cluster,
            partition=_t_partition,
            failure_type=_t_classification,
            log_size_chars=_t_log_size,
            log_truncated=_t_log_truncated,
            flags={"ticket": ticket, "raw": raw, "context_supplied": bool(context)},
            context_files_count=_t_ctx_count,
            latency_analysis_ms=_t_latency_analysis,
            latency_ticket_ms=_t_latency_ticket,
            proxy_reachable=_t_proxy_ok,
            error=_t_error,
        )

    console.print()
    if raw:
        click.echo(result)
        if ticket:
            click.echo("\n---\n")
            click.echo(ticket_text)
            click.echo(
                "\n[Review the ticket above before submitting. Add any additional "
                "context that may help the administrators (e.g. whether this failure "
                "has occurred before, steps already taken, or other relevant "
                "observations).]"
            )
        click.echo(
            "\n[AI NOTICE: This report was generated by an AI assistant. "
            "The information provided may be incomplete or incorrect. "
            "Please verify all findings and suggestions before taking action.]"
        )
    else:
        console.print(Panel(
            Markdown(result),
            title=f"[bold cyan]Job Analysis[/bold cyan]  —  {logfile.name}",
            border_style="cyan",
            padding=(1, 2),
        ))
        if ticket:
            console.print()
            console.print(Panel(
                ticket_text,
                title="[bold yellow]Suggested Help Desk Ticket[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            ))
            console.print()
            console.print(_TICKET_USER_REMINDER)
        console.print()
        console.print(_AI_DISCLAIMER)


@cli.command("models")
@click.option("--socket", "socket_path", default=_DEFAULT_SOCKET)
@click.option("--host", default=_DEFAULT_HOST)
@click.option("--port", default=_DEFAULT_PORT)
def list_models_cmd(socket_path, host, port):
    """List available AI models."""
    with _make_client(socket_path, host, port) as client:
        if not _check_proxy(client):
            raise click.ClickException(
                "Cannot reach the hpc-job-analyst proxy. Run: analyze-job proxy status"
            )
        r = client.get("/api/v1/models")
        r.raise_for_status()
        data = r.json()

    console.print()
    console.print("[bold]Available AI Models[/bold]")
    console.print()
    for m in data.get("data", []):
        console.print(f"  [cyan]{m['id']:40s}[/cyan]  {m.get('owned_by','')}")
    console.print()


# ---------------------------------------------------------------------------
# Proxy management sub-commands
# ---------------------------------------------------------------------------
@cli.group("proxy")
def proxy_group():
    """Manage the local hpc-job-analyst proxy server."""
    pass


@proxy_group.command("status")
@click.option("--socket", "socket_path", default=_DEFAULT_SOCKET)
@click.option("--host", default=_DEFAULT_HOST)
@click.option("--port", default=_DEFAULT_PORT)
def proxy_status(socket_path, host, port):
    """Check whether the proxy is running and reachable."""
    with _make_client(socket_path, host, port) as client:
        ok = _check_proxy(client)

    if ok:
        console.print("[green]hpc-job-analyst proxy is running and reachable.[/green]")
        console.print(f"  [dim]Connected via: "
                      + (f"Unix socket {socket_path}" if socket_path and
                         Path(socket_path).is_socket() else f"TCP {host}:{port}")
                      + "[/dim]")
    else:
        console.print("[red]Proxy is NOT reachable.[/red]")
        console.print(f"  TCP checked:    {host}:{port}")
        if socket_path:
            console.print(f"  Socket checked: {socket_path}")
        cfg_paths = "\n".join(f"    {p}" for p in _config_search_paths())
        console.print(f"\n  Config files searched:\n{cfg_paths}")
        sys.exit(1)


def _find_python(hint: str) -> str:
    """Return the best available Python path, preferring the user's hint."""
    import shutil
    candidates = [
        hint,
        str(Path.home() / ".conda/envs/hpc-job-analyst/bin/python"),
        "/usw/conda/miniforge/envs/hpc-job-analyst/bin/python",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    # last resort: whatever is on PATH
    return shutil.which("python3") or hint


@proxy_group.command("install")
@click.option("--proxy-script", required=True,
              type=click.Path(exists=True, path_type=Path),
              help="Path to proxy.py")
@click.option("--env-file", required=True,
              type=click.Path(path_type=Path),
              help="Path to the environment file containing USAI_API_KEY (will be created if absent).")
@click.option("--python", "python_path",
              default="/usw/conda/miniforge/envs/hpc-job-analyst/bin/python",
              show_default=True,
              help="Absolute path to the Python interpreter in the conda env.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Host the proxy listens on (written to client config).")
@click.option("--port", default=_DEFAULT_PORT, show_default=True,
              help="TCP port the proxy listens on.")
@click.option("--socket-path", default="", show_default=False,
              help="Unix socket path (optional; leave blank to use TCP only).")
def proxy_install(proxy_script, env_file, python_path, host, port, socket_path):
    """Install the hpc-job-analyst proxy as a user systemd service.

    After running this command, follow the printed instructions to
    enable linger and start the service.
    """
    python_path = _find_python(python_path)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_file = unit_dir / "hpc-job-analyst-proxy.service"

    # --- proxy env file (holds the API key) ---
    env_file = env_file.expanduser().resolve()
    if not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(
            "# hpc-job-analyst proxy environment file\n"
            "# Set your API key below.  Protect this file: chmod 600\n"
            "USAI_API_KEY=REPLACE_WITH_YOUR_KEY\n"
            f"USAI_BASE_URL=https://api.doc.usai.gov\n"
            f"PROXY_HOST={host}\n"
            f"PROXY_PORT={port}\n"
            + (f"PROXY_SOCKET={socket_path}\n" if socket_path else "")
            + "LOG_LEVEL=INFO\n"
        )
        env_file.chmod(0o600)
        console.print(f"[yellow]Created stub env file:[/yellow] {env_file}")
        console.print("[yellow]Edit it and set USAI_API_KEY before starting the service.[/yellow]")
    else:
        env_file.chmod(0o600)
        console.print(f"[green]Using existing env file:[/green] {env_file}")

    # --- systemd unit file ---
    unit_content = textwrap.dedent(f"""\
        [Unit]
        Description=hpc-job-analyst AI proxy
        After=network.target

        [Service]
        Type=simple
        EnvironmentFile={env_file}
        ExecStart={python_path} {proxy_script.resolve()}
        Restart=on-failure
        RestartSec=10
        PrivateTmp=true

        [Install]
        WantedBy=default.target
    """)
    unit_file.write_text(unit_content)
    console.print(f"[green]Wrote systemd unit:[/green] {unit_file}")

    # --- client config file (install-prefix tier) ---
    prefix = _install_prefix()
    client_cfg_path = prefix / "etc" / "hpc-job-analyst" / "client.conf"
    client_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    client_cfg_content = (
        "# hpc-job-analyst client configuration\n"
        "# This file is read by analyze-job to locate the proxy.\n"
        "# User overrides can be placed in ~/.config/hpc-job-analyst/client.conf\n\n"
        "[proxy]\n"
        f"host = {host}\n"
        f"port = {port}\n"
        + (f"socket = {socket_path}\n" if socket_path else "# socket =\n")
        + "# model = claude_4_6_sonnet\n"
        + "# system_context_file = /path/to/extra-context.md\n"
        + "# telemetry = true\n"
    )
    client_cfg_path.write_text(client_cfg_content)
    console.print(f"[green]Wrote client config:[/green] {client_cfg_path}")

    # --- instructions ---
    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print()
    console.print("  1. Edit your env file and set USAI_API_KEY:")
    console.print(f"       [cyan]nano {env_file}[/cyan]")
    console.print()
    console.print("  2. Reload systemd and start the proxy:")
    console.print("       [cyan]systemctl --user daemon-reload[/cyan]")
    console.print("       [cyan]systemctl --user start hpc-job-analyst-proxy[/cyan]")
    console.print("       [cyan]systemctl --user status hpc-job-analyst-proxy[/cyan]")
    console.print()
    console.print("  3. Enable the proxy to start automatically at login,")
    console.print("     and survive after you log out (requires linger):")
    console.print("       [cyan]systemctl --user enable hpc-job-analyst-proxy[/cyan]")
    console.print("       [cyan]loginctl enable-linger $USER[/cyan]")
    console.print()
    console.print("  [dim]Note: linger may require a request to your HPC system administrators\n"
                  "  if self-service loginctl is not available.[/dim]")
    console.print()
    console.print("  4. Verify it is running:")
    console.print("       [cyan]analyze-job proxy status[/cyan]")
    console.print()
    console.print("  5. Optionally, place a system-context.md file at:")
    console.print(f"       [cyan]{prefix / 'etc' / 'hpc-job-analyst' / 'system-context.md'}[/cyan]")
    console.print("     to provide site- and workflow-specific guidance to the AI.")
    console.print("     Users may also add their own at:")
    console.print("       [cyan]~/.config/hpc-job-analyst/system-context.md[/cyan]")


if __name__ == "__main__":
    cli()
