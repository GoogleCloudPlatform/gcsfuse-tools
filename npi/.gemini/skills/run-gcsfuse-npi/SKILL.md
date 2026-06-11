---
name: run-gcsfuse-npi
description: >
  Orchestrates running the GCSFuse NPI benchmark suite on GCE VM and GKE TPU cluster
  concurrently, with persistent SSH connection management and state recovery.
---

# Run GCSFuse NPI

This skill guides you through executing and monitoring the GCSFuse NPI benchmark suite concurrently on GCE and GKE platforms. It uses persistent SSH sockets for communication, supports smoke testing (minimal iteration loops), and relies on a local orchestrator to monitor and resume runs upon interruption.

## Step 0: User Inputs and Configuration

The agent MUST request the following parameters from the user at the start of the task. Do NOT assume any hardcoded defaults:
1.  **Target Platforms & Topology**:
    *   Is this a **GCE VM** benchmark run, a **GKE** benchmark run, or both?
    *   For GKE: is it a **TPU** cluster or a **non-TPU** cluster node pool?
2.  **GCE VM target details**:
    *   VM Name and GCP Zone.
    *   Local SSD mount path (e.g. `/mnt/lssd` or `/tmp/npi_buffer` if no SSD is available).
    *   Whether the VM has local SSDs (`has_ssd`: true/false).
    *   Whether the target bucket is a RAPID bucket (`is_rapid_bucket`: true/false).
3.  **GKE target details**:
    *   Intermediate GCE VM Name & GCP Zone (used to trigger `npi_gke.py`).
    *   GKE Cluster Name and location/region (e.g. `europe-west4-a`).
    *   Whether the GKE cluster has local SSDs configured (`has_ssd`: true/false).
    *   Whether the target bucket is a RAPID bucket (`is_rapid_bucket`: true/false).
4.  **GCS Buckets**:
    *   Regional Bucket Name.
    *   RAPID (zonal) Bucket Name.
5.  **GCP Project**:
    *   Google Cloud Project ID (e.g. `gcs-fuse-test`).

Once you receive these values:
1.  Verify the settings.
2.  Populate `targets.json` with the corresponding target details. Example format:
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
3.  Use these resolved target names and zones in Step 1 to establish persistent SSH connections.

## Prerequisites

Before running the suite, ensure:
1.  **Local Tools**: `gcloud` and `kubectl` are authenticated locally.
2.  **Zonal/RAPID Bucket Quota & Access Whitelisting**:
    *   If using RAPID buckets (Zonal GCS), verify the project has sufficient zonal storage class quota and is whitelisted for the RAPID class.
    *   Ensure gRPC / Direct Path IPv6 traffic (port 14002) is supported and routed correctly. If you experience continuous `GOAWAY received` connection idle failures, fall back to standard Regional buckets.
3.  **Storage Buffers and Cache Size Validation**:
    *   **GCE VM**: Verify that Local SSD RAID0 array `/mnt/lssd` is successfully mounted and has sufficient space (>2TiB for a full run) to avoid filling the boot disk, which will fail the run.
    *   **GKE TPU Node**: TPU nodes lack local SSDs. Ensure memory volumes are enabled (using the `--use-memory-volumes` flag in `npi_gke.py` to mount `emptyDir` on `Memory`) to prevent cache/buffer exhaustion on GKE boot disks. **Crucial**: Since the buffer is mounted in memory (RAM), always skip running file-cache tests (`read_file_cache`) on GKE TPU cluster runs to prevent host Out-of-Memory (OOM) crashes. *(Note: On standard GKE clusters with attached local SSDs, GKE automatically provisions, formats, and mounts them; no manual RAID0 setup is required).*
4.  **GKE Cluster Node Configuration**:
    *   Verify that the GKE TPU cluster has at least one standard CPU compute node (e.g. in `default-pool`) in addition to the TPU node pool. CPU nodes are required to host GKE system services, DNS, and the GCSFuse CSI Driver controller pod. The orchestrator automatically validates that both CPU and TPU nodes are active in the GKE cluster before starting GKE workflows.
5.  **Command Execution Logging**:
    *   Ensure the runner logs all local and remote commands executed to a detailed log file (e.g. `npi_commands.log`) for transparency and diagnostic auditing.


---

## Step 1: Establish Persistent SSH Connections

To speed up command routing and insulate runs from network dropouts, establish persistent Master connections:

1.  Create the socket cache directory if missing:
    ```bash
    mkdir -p ~/.ssh/sockets
    ```
2.  Start the persistent master socket connection for each configured target VM:
    For each target VM in `targets.json`, start a background persistent SSH multiplexing connection:
    ```bash
    ssh -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com
    ```
    *Note: Always include the identity option `-i ~/.ssh/google_compute_engine` to ensure smooth authentication.*
4.  Verify socket presence:
    ```bash
    ls -la ~/.ssh/sockets/
    # Should display c4.sock and c4-ssd.sock
    ```

---

## Step 2: Customize/Tweak Benchmarks (For Fast Verification)

If running a quick verification/smoke test rather than baseline benchmarks:

1.  Edit [fio/read_matrix.csv](./fio/read_matrix.csv) to contain only:
    ```csv
    READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES
    read,1M,1M,1
    randread,1M,1M,1
    ```
2.  Edit [fio/write_matrix.csv](./fio/write_matrix.csv) to contain only:
    ```csv
    FILE_SIZE,BLOCK_SIZE,NR_FILES
    1M,1M,1
    ```
3.  Build and push the benchmark images to Artifact Registry:
    ```bash
    python3 build_images.py --project gcs-fuse-test --image-version smoke-test --gcsfuse-version v3.8.0
    ```
4.  Restore local matrices to avoid git pollution:
    ```bash
    git restore fio/read_matrix.csv fio/write_matrix.csv
    ```

---

## Step 3: Configure Target GCE VM

For each GCE VM target that requires preparation (e.g. if freshly created or reset):

1.  **Configure Storage Buffer**:
    *   **If local SSDs are present**: Create and mount the RAID0 SSD array:
        ```bash
        ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash -s" < raid0-script.sh
        ```
    *   **If NO local SSDs are present** (mocking SSD on boot disk): Mount a `tmpfs` RAM disk to act as a safe buffer. This prevents GCSFuse file clearing scripts from touching critical files in the global `/tmp` directory:
        ```bash
        ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "sudo mkdir -p /tmp/npi_buffer && sudo mount -t tmpfs -o size=1G tmpfs /tmp/npi_buffer && sudo chown -R \$USER:\$USER /tmp/npi_buffer"
        ```
2.  **Install Docker & Setup sudoless permissions**:
    *   Install the Docker engine:
        ```bash
        ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh && sudo usermod -aG docker \$USER && rm get-docker.sh"
        ```
3.  **Critical**: Close and recreate the SSH master socket (`rm -f ~/.ssh/sockets/<TARGET_NAME>.sock` and run the Step 1 command again) so the `docker` group membership takes effect!
4.  **Configure Artifact Registry Authentication**:
    *   Authorize the Docker daemon to pull images from Google Artifact Registry:
        ```bash
        ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "gcloud auth configure-docker us-docker.pkg.dev -q"
        ```

---

## Step 4: Execute & Monitor the Orchestrated Benchmarks

1.  Reset local/remote state files:
    ```bash
    rm -f ~/.npi/npi_run_state.json
    ```
2.  Start the orchestrator:
    ```bash
    python3 npi_orchestrator.py --benchmarks "read_grpc write_grpc" --image-version smoke-test --iterations 2
    ```
3.  To monitor progress, inspect the state file:
    `cat ~/.npi/npi_run_state.json`
    *   **Hang Protection**: The orchestrator enforces a 10-minute log inactivity timeout (600 seconds). If GCSFuse or FIO hangs (e.g., due to Direct Path network/gRPC connection drops or quota blocks), the orchestrator will automatically terminate the remote process tree and mark the run as `FAILED`.
    *   **Disk Space Monitoring**: For GCE runs, the orchestrator polls disk space usage of `/mnt/lssd` using `df` (which accurately captures block usage, including open-but-deleted streaming write temp files). If utilization exceeds 85%, the run is aborted to prevent out-of-disk failures.
    *   **Resilience & Connection Recovery**: If the local shell crashes or the network drops, the remote runner processes (running via `nohup` in the background) continue executing on the VMs. Retriggering the orchestrator will automatically load the state file, check if the remote PIDs are still active, skip VM prep, and resume monitoring them.
    *   **Clean Retrigger & Cleanup**: To force a fresh benchmark run, delete the local state file (`rm -f ~/.npi/npi_run_state.json`). The orchestrator will detect the `PENDING` state and perform a startup cleanup: terminating remote script processes, removing active FIO Docker containers on GCE, deleting existing benchmark jobs on the GKE cluster (via the `app=gcsfuse-npi-benchmark` label selector), and syncing the latest local scripts to the VMs before launching.


---

## Step 5: Extract and Report Performance

Once the orchestrator outputs `SUCCESS`:

1.  **Extract Custom Run Results**:
    To extract the performance results of a custom benchmark run, execute a `bq` query command. 
    
    > [!IMPORTANT]
    > **JSON Key Spacing**: In the FIO JSON output, the version is stored under the key `"fio version"` (with a space). Always query it using the quoted format: `JSON_VALUE(fio_json_output, '$."fio version"')` to avoid returning `NULL`.
    
    Run the following query substituting your Cloud Project, Dataset ID, and Table Name (e.g. `fio_read_grpc` or `fio_write_grpc`):
    ```bash
    bq query --project_id=<PROJECT_ID> --use_legacy_sql=false \
    "SELECT
      run_timestamp,
      iteration,
      JSON_VALUE(fio_json_output, '\$.\"fio version\"') AS fio_version,
      AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) / 1024.0 AS avg_read_bw_mib,
      AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64)) / 1024.0 AS avg_write_bw_mib
    FROM
      \`<PROJECT_ID>.<DATASET_ID>.<TABLE_ID>\`,
      UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
    GROUP BY 1, 2, 3
    ORDER BY run_timestamp DESC"
    ```
2.  **Compare Against Baselines (Optional)**:
    If running comparison baseline tests, you can execute the results comparison script:
    ```bash
    python3 query_results.py
    ```
3.  Compile the throughput comparison report.
4.  **High-Performance Machine Type Verification**:
    *   Check if the GCE VM or GKE node machine type used in the test (e.g., `c4-standard-96`, `ct6e-standard-4t`) is classified under the high-performance machine types in the GCSFuse `params.yaml` configuration file (located in the main GCSFuse repository).
    *   If the machine type is missing, raise a Pull Request (PR) in the GCSFuse repository to add it. This ensures GCSFuse dynamically applies optimal configuration defaults (such as Direct Path, large connection pools, and high read-ahead) for this machine family in production.

## Step 6: Step-by-Step Execution Verification Checklist

To ensure that the executing agent does not skip or miss any critical setup, run, or validation steps, they **MUST** include a checklist in their final run report marking off the following validations:

- [ ] **Prerequisites Verification**:
  - GCE boot disk size verified (>= 200GB).
  - GCE RAID0 SSD mount verified at `/mnt/lssd`.
  - Target VM and GCS bucket colocation verified (same zone for RAPID bucket, same region for regional bucket).
  - Target GCS bucket verified to have Hierarchical Namespace (HNS) enabled.
  - GKE TPU cluster node requirements verified (at least 1 CPU node + 1 TPU node active).
  - Remote python output buffering set to unbuffered (`python3 -u` verified).
- [ ] **Startup Cleanup**:
  - Orphaned background runners terminated on GCE and GKE VMs.
  - Active GCE benchmark Docker containers stopped and deleted.
  - Active Kubernetes jobs matching `app=gcsfuse-npi-benchmark` deleted.
  - Latest local code changes successfully synchronized to both VMs.
- [ ] **Disk & Log Activity Monitoring**:
  - Log activity monitored dynamically (timeouts set to 600s).
  - Disk utilization loop successfully verified (checking block allocation size to catch open-but-deleted write files).
- [ ] **Run Progress Verification**:
  - Successfully statefully recovered execution status using `npi_run_state.json` (if connection drops).
  - All requested FIO and Go Client benchmarks completed (verifying completions in `kubectl get jobs` and `ps` exit statuses).
- [ ] **Report & Verification**:
  - Throughput metrics extracted from BigQuery tables for both protocols (HTTP1 & gRPC).
  - Target machine type classification checked in main GCSFuse repository `params.yaml`.
