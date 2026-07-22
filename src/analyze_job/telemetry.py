"""
telemetry.py — Usage telemetry for analyze-job (alpha).

Each invocation appends one JSON record to a per-node JSONL spool file under:
    <install_prefix>/var/telemetry/<hostname>.jsonl

The file is created on first use.  Records are written atomically (line-at-a-time
with flush+fsync) so concurrent writes from the same node are safe.

The spool directory is on the shared filesystem, so a separate collector job
can aggregate records from all nodes without needing inter-node TCP.

Schema version 1 fields:
    schema_version      int     always 1
    timestamp_utc       str     ISO-8601 UTC
    tool_version        str     analyze_job.__version__
    hostname            str     socket.gethostname()
    username            str     os.getlogin() / pwd fallback
    model               str     model ID used for analysis
    workflow_detected   str     result of _detect_workflow(), or null
    cluster             str     from Slurm metadata, or "unknown"
    partition           str     from Slurm metadata, or "unknown"
    failure_type        str     extracted from AI classification tag, or null
    log_size_chars      int     characters in the (possibly truncated) log
    log_truncated       bool    whether the log was truncated before sending
    flags               dict    {ticket, raw, context_supplied}  (bool values)
    context_files_count int     number of system context files loaded
    latency_analysis_ms int     wall-clock ms for the analysis API call
    latency_ticket_ms   int|null  ms for ticket API call, or null
    proxy_reachable     bool    whether the proxy was reached
    error               str|null  exception class name if the tool failed, else null
"""

import json
import os
import pwd
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Spool path resolution
# ---------------------------------------------------------------------------

def _spool_dir(install_prefix: Path) -> Path:
    return install_prefix / "var" / "telemetry"


def _spool_file(install_prefix: Path) -> Path:
    hostname = socket.gethostname()
    return _spool_dir(install_prefix) / f"{hostname}.jsonl"


# ---------------------------------------------------------------------------
# Username helper (never raises)
# ---------------------------------------------------------------------------

def _username() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        pass
    try:
        return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_record(
    install_prefix: Path,
    *,
    tool_version: str,
    model: str,
    workflow_detected: Optional[str],
    cluster: str,
    partition: str,
    failure_type: Optional[str],
    log_size_chars: int,
    log_truncated: bool,
    flags: dict,
    context_files_count: int,
    latency_analysis_ms: int,
    latency_ticket_ms: Optional[int],
    proxy_reachable: bool,
    error: Optional[str],
) -> None:
    """
    Append one telemetry record to the per-node spool file.
    Never raises — all exceptions are silently swallowed so telemetry
    failures never affect the user-facing output.
    """
    record = {
        "schema_version":       1,
        "timestamp_utc":        datetime.now(timezone.utc).isoformat(),
        "tool_version":         tool_version,
        "hostname":             socket.gethostname(),
        "username":             _username(),
        "model":                model,
        "workflow_detected":    workflow_detected,
        "cluster":              cluster,
        "partition":            partition,
        "failure_type":         failure_type,
        "log_size_chars":       log_size_chars,
        "log_truncated":        log_truncated,
        "flags":                flags,
        "context_files_count":  context_files_count,
        "latency_analysis_ms":  latency_analysis_ms,
        "latency_ticket_ms":    latency_ticket_ms,
        "proxy_reachable":      proxy_reachable,
        "error":                error,
    }
    try:
        spool = _spool_file(install_prefix)
        spool.parent.mkdir(parents=True, exist_ok=True)
        with spool.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass  # telemetry must never break the tool
