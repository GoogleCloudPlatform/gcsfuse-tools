---
name: benchmark-suite-execution
description: Guides on configuring, executing, monitoring, and exporting results of GCSFuse NPI benchmark runs on GCE and GKE.
---

# Benchmark Suite Execution for GCSFuse NPI

This skill guides you through defining target environments, executing benchmarks concurrently on GCE and GKE, monitoring execution for hangs/failures, and verifying BigQuery export status.

## Step 1: Configuration & Targets Setup

Before starting a run, collect target details and populate `targets.json` in the root configuration directory.

### A. Collect Inputs
1.  **Target Platforms**: Determine if running on GCE VM, GKE cluster, or both.
2.  **GCE VM Details** (if applicable): VM Name, zone, SSD presence, SSD mount path (e.g. `/mnt/lssd` or `/tmp/npi_buffer`), and RAPID bucket usage.
3.  **GKE Details** (if applicable): Intermediate VM details, Cluster name, region/zone, SSD/RAM configuration, RAPID bucket usage, node selectors, resource limits.
4.  **GCS Buckets**: Target regional and/or RAPID (zonal) bucket names.
5.  **GCP Project**: GCP Project ID (e.g. `gcs-fuse-test`).

### B. Configure `targets.json`
Populate `targets.json` with the corresponding target details. Format:
```json
[
  {
    "name": "gce-c4-ssd",
    "type": "gce",
    "vm_name": "<GCE_VM_NAME>",
    "zone": "<GCE_ZONE>",
    "bucket": "<REGIONAL_BUCKET>",
    "dataset": "<BQ_DATASET_PREFIX>",
    "buffer_mount": "<SSD_MOUNT_PATH>",
    "has_ssd": true,
    "is_rapid_bucket": false
  },
  {
    "name": "gke-tpu-slice",
    "type": "gke",
    "vm_name": "<GKE_INTERMEDIATE_VM_NAME>",
    "zone": "<GKE_INTERMEDIATE_VM_ZONE>",
    "cluster_name": "<GKE_CLUSTER_NAME>",
    "location": "<GKE_CLUSTER_LOCATION>",
    "bucket": "<REGIONAL_BUCKET>",
    "dataset": "<BQ_DATASET_PREFIX>",
    "node_selector": "cloud.google.com/gke-accelerator-count=4,cloud.google.com/gke-nodepool=ct6e-pool,cloud.google.com/gke-tpu-accelerator=tpu-v6e-slice,cloud.google.com/gke-tpu-topology=2x2",
    "resources_limits": "google.com/tpu=4",
    "has_ssd": false,
    "is_rapid_bucket": true
  }
]
```

## Step 2: Prerequisites Validation

1.  **Local Authentication**: Ensure `gcloud` and `kubectl` are configured locally.
2.  **Access Verification**: Verify direct path routing and quota if using RAPID buckets.
3.  **Logging**: Ensure all execution commands are logged locally/remotely to `npi_commands.log`.

## Step 3: Execute Orchestrated Benchmarks

1.  **State Reset**:
    *   **Clean Run / Retrigger**: If starting a completely fresh run or recovering from a corrupted state, clean up state files:
        ```bash
        rm -f ~/.npi/npi_run_state.json
        ```
        *(This forces a clean retry, terminating active containers, mounts, and jobs before sync and relaunch).*
    *   **Resume Run**: To resume an active background run without starting over, keep the state file intact.

2.  **Execute Orchestrator**:
    Run the orchestrator script, specifying the benchmarks, image version, and iterations:
    ```bash
    python3 npi_orchestrator.py --benchmarks "<BENCHMARK_LIST>" --image-version <IMAGE_VERSION> --iterations <ITERATION_COUNT>
    ```
    Examples of `<BENCHMARK_LIST>`: `read_parallel,write_parallel` or `all`.

## Step 4: Monitor and Safety Policies

Observe logs for the following active safety policies enforced by the orchestrator:

1.  **Inactivity Timeout**:
    *   Logs are monitored continuously.
    *   If no log output is detected for 10 minutes (600s), the run is automatically aborted to protect against hangs.
2.  **Disk Space Protection**:
    *   If target storage buffer disk space exceeds 85%, GCE runs are immediately aborted to prevent out-of-disk failures.
3.  **GKE TPU Memory Management**:
    *   Ensure `--use-memory-volumes` flag is enabled in `npi_gke.py` to mount buffers in RAM.
    *   Avoid running file cache tests (`read_file_cache`) on TPU slices to prevent host Out-Of-Memory (OOM) situations.

## Step 5: Verify BigQuery Results Export

Upon successful completion, the orchestrator uploads FIO and Go Client JSON output to BigQuery.
Verify the upload:
1.  Locate the dataset: `<BQ_DATASET_PREFIX>` configured in `targets.json`.
2.  Query the BQ table using bq tool or Google Cloud Console:
    ```sql
    SELECT COUNT(*) FROM `<PROJECT_ID>.<BQ_DATASET_PREFIX>_dataset.fio_results` WHERE image_version = '<IMAGE_VERSION>'
    ```
3.  Ensure the count matches the expected number of iterations and test runs.
