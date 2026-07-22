# Changelog

All notable changes to this project will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2025-07-22

### Added

- Usage telemetry (`src/analyze_job/telemetry.py`): each invocation appends
  a JSONL record to `<install_prefix>/var/telemetry/<hostname>.jsonl`.
  Records are written atomically (flush + fsync).  Telemetry failures are
  silently swallowed and never affect the user-facing output.
- `--no-telemetry` flag on the `analyze` subcommand to opt out of telemetry
  for a single invocation.
- `telemetry` option in `client.conf` to disable telemetry system-wide
  (default: `true`).
- AI classification tag: the system prompt now instructs the model to append
  a machine-readable `<!-- CLASSIFICATION: infrastructure|model|success|unknown -->`
  tag to every analysis response.  The tag is extracted for telemetry and
  stripped before display so it never appears in user output.
- Latency timing for both the analysis and ticket API calls; recorded in
  telemetry as `latency_analysis_ms` and `latency_ticket_ms`.

### Changed

- `_analyze()` now returns `(text, classification, latency_ms)` tuple.
- `_generate_ticket()` now returns `(text, latency_ms)` tuple.
- Spinner label changed from "Analyzing with USAi..." to "Analyzing with AI..."
  to remove vendor-specific language from the visible output.
- `var/` added to `.gitignore` (telemetry spool directory).

## [0.1.0] — 2025-07-21

### Added

- `analyze-job` CLI tool for analyzing HPC job stdout files via an AI proxy.
- Structured analysis output: root cause, failure type, exact error messages,
  segment/simulation date, and recommended next steps.
- Optional `--ticket` flag to generate a draft help desk ticket with all
  information needed for HPC admins to investigate.
- System context file support: site-specific and workflow-specific Markdown
  files injected into the AI system prompt at runtime, loaded from up to four
  configurable locations (installer prefix, `/etc`, user home, explicit path).
- Workflow-aware analysis: context guidance is applied only to the detected
  workflow; cross-workflow contamination is explicitly prohibited in the prompt.
- Job metadata collection from Slurm (`scontrol` / `sacct` fallback):
  cluster, partition, node list, start/end times.
- AI disclaimer printed at the end of all output.
- User reminder to review and supplement draft tickets before submitting.
- `analyze-job proxy install` — installs the proxy as a user systemd service.
- `analyze-job proxy status` — checks proxy reachability.
- `analyze-job models` — lists available AI models.
- Multi-tier client configuration (`/etc`, install prefix, `~/.config`).
- MkDocs + Material documentation site with ReadTheDocs support.
- MIT License.

### Notes

- Initial release.  The project was previously developed internally as
  `usai-hpc-proxy`; this release renames it `hpc-job-analyst` and
  generalizes it for use on any Slurm-based HPC system.
