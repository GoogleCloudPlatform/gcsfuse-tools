# GCSFuse FIO Benchmark Runner

## Overview

This Python script automates the process of benchmarking GCSFuse performance using the Flexible I/O Tester (FIO). It handles the entire workflow from setting up dependencies to running the benchmarks and cleaning up resources.

The script performs the following actions:
1.  **Dependency Installation**: Installs `git`, `fio`, and `fuse` if they are not present (supports Debian/Ubuntu and RHEL/CentOS based systems).
2.  **GCSFuse Setup**: Clones the GCSFuse GitHub repository, checks out a specific version (branch, tag, or commit), and builds the binary.
3.  **GCS Bucket Management**: Creates a temporary GCS bucket for the test and deletes it upon completion.
4.  **Mounting**: Mounts the GCS bucket using the built GCSFuse binary and specified flags.
5.  **Benchmarking**: Runs FIO tests against the mounted directory based on a provided FIO configuration file for a specified number of iterations.
6.  **Cleanup**: Unmounts the GCSFuse directory and deletes the GCS bucket, ensuring a clean state.

## Prerequisites

Before running the script, ensure you have the following installed and configured:

-   **Python 3.8+**
-   **Go (1.21 or newer)**: The script requires Go to build GCSFuse from source.
-   **Google Cloud SDK (`gcloud`)**:
    -   Authenticated: `gcloud auth login`
    -   Project configured: `gcloud config set project <YOUR_PROJECT_ID>`
-   **BigQuery Client Library** (Optional): If you plan to upload results to BigQuery, install the client library: `pip3 install google-cloud-bigquery`. You will also need to be authenticated for application-default credentials: `gcloud auth application-default login`.
-   **Sudo privileges**: The script requires `sudo` to install packages and clear system caches.

## Usage

The script is invoked from the command line with several arguments to control the benchmark run.

```bash
python3 run_fio_benchmark.py [OPTIONS]
```

### Arguments

-   `--gcsfuse-version`: (Required) The GCSFuse version to test (e.g., `v1.2.0`, `master`, or a commit hash).
-   `--project-id`: (Required) Your Google Cloud Project ID.
-   `--location`: (Required) The GCP location (region or zone) for the GCS bucket (e.g., `us-central1`).
-   `--fio-config`: (Required) Path to the FIO configuration file.
-   `--gcsfuse-flags`: (Optional) Flags for GCSFuse, enclosed in quotes (e.g., `"--implicit-dirs --max-conns-per-host 100"`). Default is empty.
-   `--iterations`: (Optional) Number of FIO test iterations. Default is `1`.
-   `--work-dir`: (Optional) A temporary directory for builds and mounts. Default is `/tmp/gcsfuse_benchmark`.
-   `--output-dir`: (Optional) Directory to save FIO JSON output files. Default is `./fio_results`.
-   `--skip-deps-install`: (Optional) Skip the automatic dependency installation check.
-   `--project-id`: (Optional) Project ID to upload results to. If provided, `--bq-dataset-id` and `--bq-table-id` must also be set.
-   `--bq-dataset-id`: (Optional) BigQuery dataset ID.
-   `--bq-table-id`: (Optional) BigQuery table ID.

### Example

1.  **Create an FIO config file (`sample.fio`):**

    ```ini
    [global]
    ioengine=libaio
    direct=1
    runtime=30
    time_based
    group_reporting
    filename=testfile

    [random-read-4k]
    bs=4k
    rw=randread
    size=1G

    [random-write-1m]
    bs=1m
    rw=randwrite
    size=1G
    ```

2.  **Run the benchmark script:**

    ```bash
    python3 run_fio_benchmark.py \
        --gcsfuse-version master \
        --project-id your-gcp-project-id \
        --location us-central1 \
        --fio-config ./sample.fio \
        --gcsfuse-flags "--implicit-dirs" \
        --iterations 3
    ```

## Output

The script will create an output directory (e.g., `./fio_results/`) containing the FIO results in JSON format, with one file per iteration.

-   `fio_results_iter_1.json`
-   `fio_results_iter_2.json`
-   `fio_results_iter_3.json`

These files can be parsed for detailed performance analysis.