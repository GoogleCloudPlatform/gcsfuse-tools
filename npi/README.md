# GCSfuse NPI Benchmark Runner

This directory contains the `npi.py` script, a tool for orchestrating and running GCSFuse performance benchmarks for Network Performance Improvement (NPI) analysis.

## Purpose

The `npi.py` script automates the process of running a suite of performance tests against GCSFuse. It uses Docker to create isolated environments for each benchmark run, ensuring consistent and reproducible results. The script supports various configurations, including different client protocols (`HTTP/1.1`, `gRPC`) and CPU pinning for NUMA-aware performance testing.

The results of the benchmarks are uploaded to a specified BigQuery table for easy analysis and comparison.

## Requirements

Before running the script, ensure you have the following prerequisites met:

1.  **Docker:** The script requires Docker to be installed and running on the local machine. The user running the script should have permissions to run Docker containers.
2.  **Authentication:** You must be authenticated to Google Cloud either via VM's service account or using gcloud auth.
3.  **Permissions:** The authenticated user or service account must have permissions to:
    *   Pull Docker images from Google Artifact Registry (`us-docker.pkg.dev`).
    *   Read and write to the specified GCS bucket.
    *   Create tables and insert data into the specified BigQuery dataset.
4.  **lscpu:** The `lscpu` command-line utility is required for NUMA-aware benchmarks (e.g., `read_http1_numa0_fio_bound`). This tool is typically part of the `util-linux` package. If it's not available, NUMA-pinned benchmarks will be skipped.

## Usage

The script is controlled via command-line arguments.

```sh
python3 npi.py [OPTIONS]
```

### Arguments

*   -b, --benchmarks: (Optional) A space-separated list of benchmark names to run. Use 'all' to run all available benchmarks. Defaults to 'all'.
*   `--bucket-name`: (Required) The name of the GCS bucket to use for the benchmarks.
*   `--bq-project-id`: (Required) The Google Cloud Project ID where the BigQuery dataset resides.
*   `--bq-dataset-id`: (Required) The BigQuery dataset ID to store the benchmark results.
*   `--gcsfuse-version`: (Required) The GCSfuse version to test (e.g., `'v3.4.0'`). This version is used to pull the corresponding benchmark Docker images.
*   `--iterations`: (Optional) The number of times to run each FIO test within a benchmark. Defaults to `5`.
*   `--temp-dir`: (Optional) The type of temporary directory to use for GCSfuse.
    *   `'boot-disk'` (default): Uses a temporary directory on the host's boot disk.
    *   `'memory'`: Uses a `tmpfs` mount (in-memory).
*   `--dry-run`: (Optional) If set, the script will print the Docker commands it would run without actually executing them.

To see a list of all available benchmarks, you can execute the script with a `--dry-run` flag.

## Examples

### Run a single benchmark

Run the `read` benchmark using the `HTTP/1.1` protocol.

```sh
python3 npi.py \
    --benchmarks read_http1 \
    --bucket-name my-gcs-bucket \
    --bq-project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'master'
```

### Run multiple benchmarks

Run the `write` benchmark with both `HTTP/1.1` and `gRPC` protocols.

```sh
python3 npi.py \
    --benchmarks write_http1 write_grpc \
    --bucket-name my-gcs-bucket \
    --bq-project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'v1.3.0'
```

### Run all benchmarks

Run all defined benchmarks and use an in-memory temporary directory.

```sh
python3 npi.py \
    --benchmarks 'all' \
    --bucket-name my-gcs-bucket \
    --bq-project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'master' \
    --temp-dir 'memory'
```

### Dry Run

Print the commands for all benchmarks without executing them.

```sh
python3 npi.py \
    --benchmarks 'all' \
    --bucket-name my-gcs-bucket \
    --bq-project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'master' \
    --dry-run
```