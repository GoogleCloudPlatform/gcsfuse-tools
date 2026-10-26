# GCSFuse FIO Benchmark Runners

## Overview

This directory contains the containerized FIO benchmark runners used by `npi.py` and `npi_gke.py`:

- `run_fio_benchmark.py`: Runs a single FIO configuration file across one or more iterations against a GCSFuse mount (either mounting `--bucket-name` automatically or targeting a pre-mounted `--mount-path`).
- `run_fio_matrix.py`: Runs a CSV parameter matrix (`read_matrix.csv`, `write_matrix.csv`, etc.) against a parameterized `.fio` template (`read.fio`, `write.fio`).
- `fio_benchmark_runner.py`: Shared FIO execution, directory pre-creation (`job_0 .. job_{N-1}`), output parsing, and BigQuery upload logic.

Both `read.fio` and `write.fio` parameterize `direct=${DIRECT}` and `numjobs=${NUMJOBS}`:
- `NUMJOBS` defaults to `112` (overridable via `--numjobs`, CSV `NUMJOBS`, or the `NUMJOBS` environment variable).
- `DIRECT` defaults to `1` for Standard Regional buckets and `0` when orchestrated with `--is-rapid-bucket` for Zonal RAPID buckets (overridable via `--direct`, CSV `DIRECT`, or the `DIRECT` environment variable).

## Usage

### Single FIO Configuration (`run_fio_benchmark.py`)

```bash
python3 run_fio_benchmark.py [OPTIONS]
```

#### Arguments

- `--fio-config`: (Required) Path to the FIO configuration file (e.g., `./read.fio` or `./write.fio`).
- `--project-id`: (Optional) Google Cloud Project ID (required when uploading results to BigQuery).
- `--bucket-name`: (Optional) GCS bucket name to mount via GCSFuse. Either `--bucket-name` or `--mount-path` must be provided.
- `--mount-path`: (Optional) Path to an already mounted GCS bucket (when provided, GCSFuse is not mounted/unmounted by the runner).
- `--gcsfuse-flags`: (Optional) Flags for GCSFuse as a single quoted string. Default: `""`.
- `--iterations`: (Optional) Positive integer number of FIO test iterations. Default: `1`.
- `--numjobs`: (Optional) Positive integer override for FIO `NUMJOBS` concurrency.
- `--direct`: (Optional) Override FIO `DIRECT` I/O mode (`0` or `1`).
- `--cpu-limit-list`: (Optional) Comma-separated CPU list for `taskset` pinning (e.g., `'0-3,7'`).
- `--bind-fio`: (Optional) Bind the FIO process to `--cpu-limit-list` (requires `--cpu-limit-list`).
- `--keep-mount`: (Optional) Mount GCSFuse once before iterations and keep it mounted across iterations (used for file-cache tests).
- `--work-dir`: (Optional) Working directory for temporary mount points. Default: `/tmp/gcsfuse_benchmark`.
- `--output-dir`: (Optional) Directory to save FIO JSON output files. Default: `./fio_results`.
- `--summary-file`: (Optional) File path to write the formatted summary table.
- `--bq-dataset-id`: (Optional) BigQuery dataset ID for uploading iteration results.
- `--bq-table-id`: (Optional) BigQuery table ID for uploading iteration results.

#### Example

```bash
READ_TYPE=read FILE_SIZE=1G BLOCK_SIZE=1M NR_FILES=10 \
python3 run_fio_benchmark.py \
    --mount-path /mnt/gcsfuse \
    --fio-config ./read.fio \
    --numjobs 112 \
    --direct 1 \
    --iterations 3
```

### Matrix Configuration (`run_fio_matrix.py`)

```bash
python3 run_fio_matrix.py \
    --mount-path /mnt/gcsfuse \
    --fio-template ./read.fio \
    --matrix-config ./read_matrix.csv \
    --iterations 5
```

`run_fio_matrix.py` accepts `--fio-template`, `--matrix-config`, `--summary-file-name`, and the same mount, CPU pinning, `--numjobs`, `--direct`, and BigQuery options as `run_fio_benchmark.py` (`--project-id` is optional when BigQuery upload is not requested).

## Output

Each iteration writes `fio_results_iter_<N>.json` under `--output-dir` (or per-configuration subdirectories under `--output-dir` when running `run_fio_matrix.py`).
