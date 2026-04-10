# Running NPI Benchmarks on GCE

This guide explains how to build the required Docker images and run the NPI (Network Performance Improvement) benchmarks on a Google Compute Engine (GCE) VM.

## Prerequisites

1.  **Google Cloud Project**: You need a GCP project to host your Artifact Registry, Cloud Storage bucket, and BigQuery dataset.
2.  **VM Setup**: A GCE VM with Docker installed. The VM's service account must have the following scopes:
    *   `https://www.googleapis.com/auth/bigquery`
    *   `https://www.googleapis.com/auth/devstorage.read_write`
    *   `https://www.googleapis.com/auth/cloud-platform` (or granular scopes)
3.  **System Utilities**: The `npi.py` script requires `python3`. Additionally, the `lscpu` command-line utility is required for NUMA-aware benchmarks. These are usually pre-installed, but you can ensure they are present by installing the `util-linux` and `python3` packages:
    ```bash
    sudo apt-get update && sudo apt-get install -y util-linux python3
    ```
4.  **Authentication**: If not using a VM service account, you must authenticate using `gcloud auth login` and `gcloud auth application-default login`.
5.  **BigQuery Dataset**: Create a BigQuery dataset to store the benchmark results.
6.  **Artifact Registry**: You must have an Artifact Registry repository to host the Docker images.
    ```bash
    gcloud services enable artifactregistry.googleapis.com --project=YOUR_PROJECT_ID
    gcloud artifacts repositories create gcsfuse-benchmarks \
        --repository-format=docker \
        --location=us \
        --project=YOUR_PROJECT_ID
    ```

## Step 1: Build the Benchmark Images

The NPI benchmarks use Docker containers to provide an isolated environment for running tests. You can build and push these images to your Artifact Registry using the provided `Makefile`.

By default, the `Makefile` builds for project `gcs-fuse-test`. You should either edit the `Makefile` or pass your project ID to the `make` command:

```bash
cd gcsfuse-tools/npi

# Option 1: Override project and version in the make command
make build PROJECT=YOUR_PROJECT_ID GCSFUSE_VERSION=YOUR_GCSFUSE_VERSION

# Example:
# make build PROJECT=my-project GCSFUSE_VERSION=v3.5.6

# Option 2: Edit Makefile directly to change the PROJECT variable, then run:
make build
```

This will trigger a Cloud Build job (`cloudbuild.yaml`) that builds the `fio-read-benchmark`, `fio-write-benchmark`, and `orbax-emulated-benchmark` images and pushes them to `us-docker.pkg.dev/YOUR_PROJECT_ID/gcsfuse-benchmarks/`.

## Step 2: Run the Benchmarks

Once the images are built, you can use the `npi.py` script to orchestrate the benchmark runs. The script automatically generates the correct `docker run` commands based on the desired benchmarks and configurations.

### Basic Usage

Run all available benchmarks:

```bash
python3 npi.py \
    --benchmarks 'all' \
    --bucket-name YOUR_GCS_BUCKET \
    --project-id YOUR_PROJECT_ID \
    --bq-dataset-id YOUR_BQ_DATASET_ID \
    --gcsfuse-version YOUR_GCSFUSE_VERSION

# Example:
# python3 npi.py --benchmarks 'all' --bucket-name my-bucket --project-id my-project --bq-dataset-id my_dataset --gcsfuse-version v3.5.6
```

### Specifying Benchmarks

You can specify a space-separated list of specific benchmarks to run. For example, to run only the `read_http1` and `write_grpc` benchmarks:

```bash
python3 npi.py \
    --benchmarks read_http1 write_grpc \
    --bucket-name YOUR_GCS_BUCKET \
    --project-id YOUR_PROJECT_ID \
    --bq-dataset-id YOUR_BQ_DATASET_ID \
    --gcsfuse-version YOUR_GCSFUSE_VERSION
```

### Understanding the Parameters

*   `--benchmarks`: 'all' or a list of specific benchmarks (e.g., `read_http1`, `orbax_read_grpc_numa0_fio_bound`).
*   `--bucket-name`: The GCS bucket where FIO will read/write data.
*   `--project-id`: The GCP Project ID containing your BigQuery dataset.
*   `--bq-dataset-id`: The BigQuery dataset where results will be inserted.
*   `--gcsfuse-version`: The version tag used when building the images (e.g., `v3.5.6`).
*   `--iterations`: (Optional) Number of FIO test iterations. Default is 5.
*   `--temp-dir`: (Optional) FUSE temp directory type (`boot-disk` or `memory`).

### Dry Run

To see exactly what `docker run` commands the script will execute without actually running them, add the `--dry-run` flag:

```bash
python3 npi.py \
    --benchmarks read_http1 \
    --bucket-name YOUR_GCS_BUCKET \
    --project-id YOUR_PROJECT_ID \
    --bq-dataset-id YOUR_BQ_DATASET_ID \
    --gcsfuse-version YOUR_GCSFUSE_VERSION \
    --dry-run
```

## Step 3: Analyze Results

Once the benchmarks complete, the results are populated automatically into your BigQuery dataset (each benchmark gets its own table). 

To extract useful performance characteristics such as throughput (MiB/s) and latency (ms) from the raw FIO JSON in BigQuery, refer to the [BigQuery Performance Analysis Queries](bq_queries.md) guide.
