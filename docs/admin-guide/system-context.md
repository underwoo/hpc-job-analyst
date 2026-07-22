# System Context Files

System context files teach the AI about your specific HPC system and the
scientific workflows your users run.  Without them, the tool applies only
general HPC knowledge.  With them, it can give workflow-aware analysis and
avoid suggesting steps that the workflow already handles automatically.

## How it works

When `analyze-job` runs, it loads all context files that exist and injects
their content into the AI system prompt under a clearly labeled block.  The
AI is instructed to apply guidance from each workflow section only when it
detects that workflow in the job log — guidance from one workflow is not
applied to a different workflow.

## File locations

All files that exist are merged in this order:

| Priority | Path | Who manages it |
|----------|------|----------------|
| 1 (lowest) | `<install_prefix>/etc/hpc-job-analyst/system-context.md` | Operator / installer |
| 2 | `/etc/hpc-job-analyst/system-context.md` | System-wide HPC admin |
| 3 (highest) | `~/.config/hpc-job-analyst/system-context.md` | Individual user |

A fourth file can be specified via `system_context_file` in `client.conf`
(see [Configuration](configuration.md)).  All sources that exist are merged;
none suppresses the others.

Each file is tagged with its source path in the prompt so the AI can
distinguish where guidance came from.

When `analyze-job` runs, it prints which context files were loaded:

```
System context loaded from:
  /opt/hpc-job-analyst/etc/hpc-job-analyst/system-context.md
  /home/user/.config/hpc-job-analyst/system-context.md
```

If no context files are found, nothing is printed and the tool operates on
general HPC knowledge only.

## Writing a context file

Context files are plain Markdown.  Use level-2 (`##`) headers to separate
sections.  A fully annotated template is available at
[`docs/examples/system-context.md.example`](../examples/system-context.md.example).

### Recommended sections

**System section** — describes the hardware, interconnect, filesystem,
and Slurm partition names.  Include the exact error strings that indicate
transient infrastructure failures on your system.

```markdown
## System: MyCluster

MyCluster is a Cray EX system running RHEL 9 with Slurm.
Partitions: small, medium, large, gpu.
Filesystem: Lustre on /scratch.
Interconnect: HPE Slingshot / OFI (libfabric).

Transient fabric failure signatures:
- "OFI retry limit exceeded"
- "fi_cq_read failed"
```

**Workflow sections** — one per workflow.  The most important content is
what the workflow manages automatically (so the AI doesn't suggest it) and
what the correct recovery steps are.

```markdown
## Workflow: MyWorkflow

Recognized in logs by: "MYWORKFLOW_VERSION", "myworkflow-run".

### What the workflow manages automatically

- Restart file staging and validation (do NOT suggest manual restart checks)
- Segment numbering and re-entry logic

### Recovery for transient infrastructure failures

Simply resubmit via `myworkflow-submit`.  Do not manually stage inputs.

### Recovery for model/software failures

Investigate the namelist or model configuration.  Contact the model team.
```

**General guidance** — site-wide rules regardless of workflow.

```markdown
## General guidance

- Quota errors: users check usage with `lfs quota -u $USER /scratch`.
- Node exclusion: users may add `--exclude=<node>` to their job script,
  but should resubmit first without exclusion.
```

## Deploying at a new site

When deploying `hpc-job-analyst` on a system other than the one it was
originally written for:

1. Write a new `system-context.md` describing your system's hardware,
   interconnect, filesystem, and partitions.
2. Add workflow sections for each workflow your users run on that system.
3. Place the file at `/etc/hpc-job-analyst/system-context.md` (system-wide)
   or in the install prefix `etc/` directory.
4. Test with a representative failed job log to confirm the AI gives
   appropriate advice.

The tool's prompts are generic.  All site-specific knowledge lives entirely
in the context file.
