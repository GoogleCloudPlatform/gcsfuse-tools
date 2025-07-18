# GCSFuse Benchmarking Framework

## Overview

This directory provides a framework to:

- Set up and tear down a Google Compute Engine (GCE) VM for benchmarking.
- Install necessary tools like FIO, GCSFuse, and Google Cloud SDK on the VM.
- Run FIO benchmarks using predefined job files.
- Monitor GCSFuse CPU and memory usage during benchmarks.
- Upload FIO output and monitoring metrics to Google Cloud Storage (GCS) Bucket and BigQuery.

---

## Setup

Before running the benchmarks, ensure you have:

- **Google Cloud SDK (`gcloud`)**:
  - Authenticated with `gcloud auth login`.
  - Configured for the correct project:

    ```bash
    gcloud config set project <PROJECT_ID>
    ```

- **Permissions**:
  - Read access to `gs://gcsfuse-release-benchmark-fio-data`.
  - Read/Write access to a GCS bucket for results (e.g., `gs://gcsfuse-release-benchmarks-results`).
  - Permissions to create/delete GCE VMs, GCS buckets, and BigQuery datasets/tables within your specified project.

- **Python Dependencies**:\
  Install the required Python packages:

    ```bash
    pip install -r perf-benchmarking-for-releases/requirements.txt
    ```

  The key Python packages include:

  - `google-cloud-bigquery`
  - `google-cloud-monitoring`
  - `requests`

---

## Usage

The main script to run the benchmarks is `run-benchmarks.sh`.  
It should be executed from the `perf-benchmarking-for-releases` directory.

### Syntax

```bash
bash run-benchmarks.sh <GCSFUSE_VERSION> <PROJECT_ID> <REGION> <MACHINE_TYPE> <IMAGE_FAMILY> <IMAGE_PROJECT>
```

### Arguments:

- `<GCSFUSE_VERSION>`: A Git tag (e.g., `v1.0.0`), branch name (e.g., `main`), or a commit ID on the GCSFuse master branch.
- `<PROJECT_ID>`: Your Google Cloud Project ID in which you want the VM and Bucket to be created.
- `<REGION>`: The GCP region where the VM and GCS buckets will be created (e.g., `us-south1`).
- `<MACHINE_TYPE>`: The GCE machine type for the benchmark VM (e.g., `n2-standard-96`). This script supports attaching 16 local NVMe SSDs (375GB each) for LSSD-supported machine types.
   - **Note:** If your machine type supports LSSD but is not included in the `LSSD_SUPPORTED_MACHINES` array within `run-benchmarks.sh` script, you may need to manually add it to ensure LSSDs are attached.
- `<IMAGE_FAMILY>`: The image family for the VM (e.g., `ubuntu-2204-lts`).
- `<IMAGE_PROJECT>`: The image project for the VM (e.g., `ubuntu-os-cloud`).

### Example:

```bash
bash run-benchmarks.sh v2.12.0 gcs-fuse-test us-south1 n2-standard-96 ubuntu-2204-lts ubuntu-os-cloud
```

---

## Workflow

1. **Unique ID Generation**:  
   A unique ID is generated based on the timestamp and a random suffix to name the VM and related GCS buckets.

2. **GCS Bucket Creation**:  
   A GCS bucket `gcsfuse-release-benchmark-data-<UNIQUE_ID>` is created in the specified region to store FIO test data.

3. **FIO Job File Upload**:  
   All `.fio` job files from the local `fio-job-files/` directory are uploaded to the results bucket.

4. **Data Transfer**:  
   A Storage Transfer Service job copies read data from `gs://gcsfuse-release-benchmark-fio-data` to the newly created test data bucket.

5. **VM Creation**:
   - A GCE VM is created with the specified machine type.
   - Boot disk size: 1000GB.

6. **`starter-script.sh` Execution**:  
   This script runs on the VM after creation. It:
   - Installs common dependencies (e.g., git, fio, python3-pip).
   - Builds GCSFuse from the specified version.
   - Sets up local SSDs if enabled.
   - Downloads FIO job files.
   - Mounts the GCS bucket using the built GCSFuse binary.
   - Monitors GCSFuse CPU and memory usage during FIO runs.
   - Executes each FIO job and saves the JSON output.
   - Uploads the FIO results and monitoring logs to GCS.
   - Calls `upload_fio_output_to_bigquery.py` to push results to BigQuery.

7. **Cleanup**:  
   A cleanup function is trapped to run on exit, ensuring the VM and the created GCS test data bucket are deleted.

---

## Output

### BigQuery

FIO benchmark results, including I/O statistics, latencies, and system resource usage (CPU/Memory), are uploaded to a BigQuery table with:

- **Project ID**: `gcs-fuse-test-ml`
- **Dataset ID**: `gke_test_tool_outputs`
- **Table ID**: `fio_outputs`

---

### Google Cloud Storage

- **FIO Test Data:** The FIO test data (copied from `gs://gcsfuse-release-benchmark-fio-data`) is uploaded to a newly created bucket dynamically named `gcsfuse-release-benchmark-data-<UNIQUE_ID>`.
- **Benchmark Results and FIO Job Files:** FIO JSON output files, benchmark logs, and FIO job files, are uploaded to the `gs://gcsfuse-release-benchmarks-results` bucket. The specific path within this bucket will be `gs://gcsfuse-release-benchmarks-results/<GCSFUSE_VERSION>-<UNIQUE_ID>/`.
- A `success.txt` file is uploaded to GCS upon successful completion of all benchmarks.
