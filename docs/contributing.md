# Contributing

Thank you for your interest in contributing to `hpc-job-analyst`.

## Development setup

```bash
git clone https://github.com/SethUnderwood/hpc-job-analyst.git
cd hpc-job-analyst
conda env create -f environment.yml
conda activate hpc-job-analyst
```

The package is installed in editable mode, so changes to
`src/analyze_job/cli.py` take effect immediately.

## Project layout

```
hpc-job-analyst/
  src/analyze_job/
    __init__.py         package marker
    cli.py              analyze-job CLI (all logic, prompts, metadata helpers)
  server/
    proxy.py            FastAPI proxy server
  docs/
    index.md            documentation home page
    user-guide/         end-user documentation
    admin-guide/        installation and administration
    examples/           example configuration files
      client.conf.example
      system-context.md.example
  mkdocs.yml            documentation site configuration
  .readthedocs.yaml     Read the Docs build configuration
  environment.yml       conda environment definition
  pyproject.toml        package metadata and entry points
  LICENSE               MIT License
  CHANGELOG.md          version history
```

## Making changes

- All CLI logic, prompts, and metadata helpers live in `src/analyze_job/cli.py`.
- The proxy server lives in `server/proxy.py`.
- Documentation lives in `docs/`.  Run `mkdocs serve` to preview locally.

## Reporting issues

Please open an issue at:
https://github.com/SethUnderwood/hpc-job-analyst/issues

Include:
- The `analyze-job` version (from `pip show hpc-job-analyst`)
- The command you ran
- The output you saw (redact any sensitive site information)
- What you expected to happen
