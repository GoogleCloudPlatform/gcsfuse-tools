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
4.  **VM Scopes (If using a GCE VM):** The service account associated with the Google Compute Engine VM must have the appropriate access scopes.
    *   **To set the scopes, run the following command from your local terminal or Cloud Shell (not from within the VM itself):**
        ```sh
        gcloud compute instances set-service-account YOUR_VM_NAME \
            --project=YOUR_GCP_PROJECT_ID \
            --zone=YOUR_VM_ZONE \
            --scopes=bigquery,storage-rw,cloud-platform
        ```
    *   Replace `YOUR_VM_NAME`, `YOUR_GCP_PROJECT_ID`, and `YOUR_VM_ZONE` with your specific values.
    *   The `cloud-platform` scope provides broad access. For a more secure setup, you can provide a comma-separated list of more restrictive scopes, ensuring at least `bigquery` and `storage-rw` are included.

4.  **lscpu:** The `lscpu` command-line utility is required for NUMA-aware benchmarks (e.g., `read_http1_numa0_fio_bound`). This tool is typically part of the `util-linux` package. If it's not available, NUMA-pinned benchmarks will be skipped.

### Authentication: Google Cloud Access

Authentication to Google Cloud is mandatory, either through a VM's service account or by utilizing `gcloud auth login`.

*   **Requirement: gcloud Authentication Status**
    *   **Install gcloud CLI:** For installation instructions, please refer to the [official gcloud CLI documentation](https://cloud.google.com/sdk/docs/install).
    *   **gcloud auth login:** `gcloud auth login`
    *   **Command to Check:** `gcloud auth list`
    *   **Expected Output/Success:** An account with an asterisk (`*`) next to it, indicating it is the active account.


### Docker: Installation and Permissions

Sudoless Docker must be installed and actively running on the local machine. The user executing the script needs to possess the necessary permissions to run Docker containers.

*   **Requirement: Docker Installed & Running**
    *   Installation instructions as per Machine type: [Install Docker Engine](https://docs.docker.com/engine/install/)
    *   Making docker sudoless: [Make docker sudoless](https://docs.docker.com/engine/install/linux-postinstall/)
*   **Requirement: User Permissions**
    *   **Command to Check a sudoless docker:** `docker run hello-world`
    *   **Expected Output/Success:** A "Hello from Docker!" message is displayed, confirming the installation is working.
*   **Requirement: Pull Docker images from Google Artifact Registry (us-docker.pkg.dev)**
    *   Execute the following command:
        ```bash
        # Configure Docker to use gcloud for GCR registries
        gcloud auth configure-docker

        # Add support for us-docker.pkg.dev
        gcloud auth configure-docker us-docker.pkg.dev
        ```

### Permissions: GCS, BigQuery

Checking permissions is a multi-step process. You'll use your active authenticated account (from the previous step) for these checks.

*   **Requirement: Read/Write GCS to the specified Bucket**
    *   **Command to Check:** `gsutil ls gs://YOUR_BUCKET_NAME`
    *   **Expected Output/Success:** A list of files in the bucket should be printed. If it fails, you likely lack the `storage.objects.list` permission.
*   **Requirement: BigQuery Access**
    *   **Command to Check:** `bq show YOUR_BQ_PROJECT_ID:YOUR_BQ_DATASET_ID`
    *   **Expected Output/Success:** Prints the details and tables of the BigQuery dataset. If it fails, you may lack `bigquery.datasets.get` or the dataset may not exist.

### lscpu Utility

The `lscpu` command-line utility is essential for NUMA-aware benchmarks (e.g., `read_http1_numa0_fio_bound`). This tool is typically included in the `util-linux` package. If it is unavailable, NUMA-pinned benchmarks will be automatically skipped.

*   **Requirement: lscpu Availability**
    *   **Command to Check:** `command -v lscpu`
    *   **Expected Output/Success:** Prints the full path to the utility (e.g., `/usr/bin/lscpu`).
*   **Requirement: Verify Functionality**
    *   **Command to Check:** `lscpu | grep 'NUMA node(s)'`
    *   **Expected Output/Success:** Shows information about NUMA nodes if available.

## Getting Started

1.  Clone the `gcsfuse-tools` repository from GitHub:
    ```bash
    git clone https://github.com/GoogleCloudPlatform/gcsfuse-tools.git
    ```
2.  Navigate to the `npi` directory:
    ```bash
    cd gcsfuse-tools/npi
    ```
## Prerequisites
### Publishing FIO Images
The FIO images can be built and published using Google Cloud Build via the provided `Makefile`, which simplifies the process.

### Project and Registry Setup

By default, the build process uses the `gcs-fuse-test` project. If you wish to use a different Google Cloud project, you must perform the following setup steps first.

1.  **Enable the Artifact Registry API:**
    ```sh
    gcloud services enable artifactregistry.googleapis.com --project=YOUR_PROJECT_ID
    ```

2.  **Create the Docker Repository:**
    The build process expects a Docker repository named `gcsfuse-benchmarks`. Create it if it doesn't exist.
    ```sh
    gcloud artifacts repositories create gcsfuse-benchmarks \
        --repository-format=docker \
        --location=us \
        --project=YOUR_PROJECT_ID
    ```
    Replace `YOUR_PROJECT_ID` with your target project ID in the commands above.

### Updating the Makefile

For a permanent change of the target project, you can edit the `Makefile` in this directory and update the `PROJECT` variable:

```makefile
# In Makefile
PROJECT=your-new-project-id
```


### Using Make (Recommended)

To build the images with default versions specified in the `Makefile`:
```sh
make
```

To override the GCSfuse version, you can pass it as a variable to the `make` command. This is useful for building images for a specific version of GCSfuse.

```sh
make GCSFUSE_VERSION=<gcsfuse_version>
```
Replace `<gcsfuse_version>` with the desired GCSfuse version (e.g., `v3.2.0`).

## Usage

The script is controlled via command-line arguments.

```sh
python3 npi.py [OPTIONS]
```

### Arguments

*   -b, --benchmarks: (Optional) A space-separated list of benchmark names to run. Use 'all' to run all available benchmarks. Defaults to 'all'.
*   `--bucket-name`: (Required) The name of the GCS bucket to use for the benchmarks.
*   `--project-id`: (Required) The Google Cloud Project ID where the BigQuery dataset resides.
*   `--bq-dataset-id`: (Required) The BigQuery dataset ID to store the benchmark results.
*   `--gcsfuse-version`: (Required) The GCSfuse version to test (e.g., `'v3.4.0'`). This version is used to pull the corresponding benchmark Docker images.
*   `--iterations`: (Optional) The number of times to run each FIO test within a benchmark. Defaults to `5`.
*   `--temp-dir`: (Optional) The type of temporary directory to use for GCSfuse.
    *   `'boot-disk'` (default): Uses a temporary directory on the host's boot disk.
    *   `'memory'`: Uses a `tmpfs` mount (in-memory).
*   `--dry-run`: (Optional) If set, the script will print the Docker commands it would run without actually executing them.

To see a list of all available benchmarks, you can execute the script with a `--dry-run` flag.

## Benchmark Glossary

The script supports multiple benchmark types. The primary category is FIO-based tests that measure GCSfuse performance. There is also a baseline test that measures raw GCS performance without GCSfuse.

*   **`go-storage-tests`**: Runs a series of read tests directly against GCS using the Go storage client. It tests both **HTTP/1.1** and **gRPC** protocols and uploads the bandwidth results for each to BigQuery. This provides a performance baseline without the overhead of FUSE.

*   **FIO Benchmarks (e.g., `read_http1`, `write_grpc_...`)**: These benchmarks use the Flexible I/O (FIO) tool to measure GCSfuse performance under various conditions. Their names follow a strict format, explained below.

---
### FIO Benchmark Naming Convention

The names of the GCSFUSE performance benchmarks follow a strict format designed to clearly communicate the **access pattern**, **protocol**, **NUMA locality**, and **CPU affinity** being tested.

---

### 1.  Operation Type

This segment identifies the **data access pattern** being measured.

| Component | Meaning | Relevance |
| :--- | :--- | :--- |
| **`read`** | Measures the basic performance of a standard file **read** operation from GCS via FUSE. | General I/O performance. |
| **`write`** | Measures the basic performance of a standard file **write** operation to GCS via FUSE. | General I/O performance. |
| **`orbax_read`** | Measures performance using an access pattern that mimics **reading an Orbax checkpoint** (a common data format in machine learning, e.g., JAX/Flax). | Critical for Deep Learning/TPU workloads. |

---

### 2.  Protocol

This identifies the underlying **communication protocol** used by GCS FUSE to talk to the Google Cloud Storage backend.

| Component | Meaning | Note |
| :--- | :--- | :--- |
| **`http1`** | Uses the standard **HTTP/1.1** protocol. | Baseline protocol. |
| **`grpc`** | Uses the **gRPC** (Remote Procedure Call) framework, which leverages **HTTP/2**. | High-performance, low-latency protocol. |

---

### 3.  NUMA Node Affinity (`numa0`, `numa1`)

**NUMA (Non-Uniform Memory Access)** is an architecture where a system's CPUs are grouped into nodes, each with its own local memory. Accessing local memory is faster than accessing memory on a remote node.

These tags test the impact of **memory locality** and should be interpreted based on your specific hardware configuration.

| Component | Meaning | Impact |
| :--- | :--- | :--- |
| **`numa0`** | The benchmark process and memory are **explicitly bound** to **NUMA Node 0**. | Tests performance with **local memory access**. |
| **`numa1`** | The benchmark process and memory are **explicitly bound** to **NUMA Node 1**. | Tests performance when memory is local to the second node. |
| (Absence of tag) | No explicit NUMA binding is applied. | OS decides placement. |

---

### 4. CPU/FIO Binding

This segment controls whether the I/O threads are explicitly **pinned** to CPU cores, often used with the **FIO** (Flexible I/O Tester) tool.

| Component | Meaning | Test Purpose |
| :--- | :--- | :--- |
| **`fio_bound`** | The FIO I/O threads are **explicitly bound** (pinned) to specific CPU cores. | Provides a **stable, isolated environment** to measure raw I/O performance with minimal CPU contention. |
| **`fio_notbound`** | The FIO I/O threads are **not explicitly bound** and are managed by the OS. | Models a more **realistic, general-purpose workload** with OS scheduling overhead. |

---

### Benchmark Example

| Benchmark Name | Detailed Meaning |
| :--- | :--- |
| **`orbax_read_grpc_numa0_fio_bound`** | Measures **Orbax checkpoint read** performance using **gRPC**, running on **NUMA Node 0**, with FIO threads **pinned** to local cores. |
| **`write_http1_numa1_fio_notbound`** | Measures **standard file write** performance using **HTTP/1.1**, running on **NUMA Node 1**, with FIO threads **unpinned** (OS manages placement). |

## Examples

### Run a single benchmark

Run the `read` benchmark using the `HTTP/1.1` protocol.

```sh
python3 npi.py \
    --benchmarks read_http1 \
    --bucket-name my-gcs-bucket \
    --project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'master'
```

### Run multiple benchmarks

Run the `write` benchmark with both `HTTP/1.1` and `gRPC` protocols.

```sh
python3 npi.py \
    --benchmarks write_http1 write_grpc \
    --bucket-name my-gcs-bucket \
    --project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'v1.3.0'
```

### Run all benchmarks

Run all defined benchmarks and use an in-memory temporary directory.

```sh
python3 npi.py \
    --benchmarks 'all' \
    --bucket-name my-gcs-bucket \
    --project-id my-gcp-project \
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
    --project-id my-gcp-project \
    --bq-dataset-id my_benchmark_dataset \
    --gcsfuse-version 'master' \
    --dry-run
```