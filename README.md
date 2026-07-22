# hpc-job-analyst

AI-assisted HPC job failure analysis tool for Slurm-based supercomputers.

`analyze-job` reads an HPC job stdout file, sends it to a local AI proxy,
and returns a structured explanation of why the job failed — along with
concrete next steps and an optional draft help desk ticket.

## Documentation

Full documentation: https://hpc-job-analyst.readthedocs.io

## Quick start

```bash
git clone https://github.com/SethUnderwood/hpc-job-analyst.git
cd hpc-job-analyst
conda env create -f environment.yml
conda activate hpc-job-analyst

analyze-job proxy install \
  --proxy-script server/proxy.py \
  --env-file ~/.config/hpc-job-analyst/env

analyze-job /path/to/myjob.o135797758
analyze-job /path/to/myjob.o135797758 --ticket
```

## License

MIT — see [LICENSE](LICENSE).
