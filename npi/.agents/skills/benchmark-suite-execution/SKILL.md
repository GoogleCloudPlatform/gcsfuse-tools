---
name: benchmark-suite-execution
description: Guides on configuring targets.json, parameterizing and executing benchmark suites on GCE VMs and GKE clusters using npi_orchestrator.py, managing state resets in ~/.npi/npi_run_state.json, enforcing active safety policies (4h inactivity timeout, 85% disk usage limit, TPU OOM avoidance), and verifying BigQuery table exports for host_info and fio_* tables.
---

# Benchmark Suite Execution for GCSFuse NPI

This skill guides you through defining target environments in `targets.json`, executing benchmarks concurrently on GCE VMs and GKE clusters using `npi_orchestrator.py`, monitoring execution against safety policies, managing run state files, and verifying BigQuery metric exports.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **Benchmark Images Pushed**: Container images built and pushed to Artifact Registry (`us-docker.pkg.dev/<PROJECT_ID>/gcsfuse-npi-images:<IMAGE_VERSION>`) via `benchmark-build-setup`.
2. **Storage Buffers Mounted**: RAID0 or `tmpfs` RAM disks mounted at configured buffer paths (`/mnt/lssd` or `/tmp/npi_buffer`).
3. **Active Master SSH Sockets**: Master SSH sockets established for target VMs at `~/.ssh/sockets/<TARGET_NAME>.sock`.
4. **CLI Tools & GCP Authentication**: `gcloud`, `kubectl`, and `bq` CLI tools configured and authenticated with access to GCS test buckets and BigQuery output datasets.
5. **Host-Level OS Tuning**: Large Receive Offload (LRO/GRO) and Receive Flow Steering (RFS/RPS) enabled on target VM network interfaces to achieve the 20 GB/s SLA gate requirement.

### Trigger Conditions
- Target VMs and GKE clusters are provisioned, configured, and ready to execute FIO or Go SDK benchmark workloads.
- Initiating automated benchmarking across GCE and GKE targets for NPI performance qualification.
- Retriggering benchmark suites after state reset or resume operation.

## Input/Output Contract

### Inputs
- **`targets.json`**: Configuration file specifying target definitions (GCE VMs, GKE clusters, buckets, dataset prefixes, buffer mount paths, node selectors).
- **CLI Flags**: `--benchmarks` (e.g., `read_parallel`, `write_parallel`, `all`), `--image-version`, `--iterations`.
- **GCS Test Buckets**: Regional and zonal (RAPID) target buckets.

### Outputs
- **`~/.npi/npi_run_state.json`**: Local run state tracking file managing workflow resumption or clean restart.
- **`npi_commands.log`**: Detailed command and output log capturing stdout/stderr across test execution steps.
- **BigQuery Tables**: Exported benchmark datasets containing:
  - `<DATASET_PREFIX>_regional.host_info` or `<DATASET_PREFIX>_zonal.host_info`
  - `<DATASET_PREFIX>_regional.fio_<benchmark_name>`
  - `<DATASET_PREFIX>_regional.go_client_read_<config>`

## Step-by-Step Procedure

### Step 1: Configuration & Targets Setup

Collect target details and populate `targets.json` in the root configuration directory.

Example `targets.json`:
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

### Step 2: Host-Level Network Tuning Verification (SLA Requirement)

To achieve the target 20 GB/s sustained throughput for both HTTP/1.1 and gRPC:
1. Verify LRO/GRO offloads and RFS/RPS packet steering are enabled on target host interfaces.
2. If host interface tuning has not been applied, refer to **[Remediation Advisor](../remediation-advisor/SKILL.md)** to tune `ethtool` and `sysctl` settings before launching benchmarks.

### Step 3: Run State Management

Manage execution state prior to invoking `npi_orchestrator.py`:
- **Clean Run / Retrigger**: If starting a fresh run or recovering from a failed/corrupted execution state, remove existing state:
  ```bash
  rm -f ~/.npi/npi_run_state.json
  ```
  *(This terminates lingering containers and mounts before relaunching).*
- **Resume Active Run**: To resume an in-progress background run without starting over, keep `~/.npi/npi_run_state.json` intact.

### Step 4: Execute Orchestrated Benchmarks

Run the orchestrator script:
```bash
python3 npi_orchestrator.py --benchmarks "<BENCHMARK_LIST>" --image-version <IMAGE_VERSION> --iterations <ITERATION_COUNT>
```
*Example benchmark lists*: `read_parallel,write_parallel` or `all`.

### Step 5: Active Monitoring & Safety Policies

`npi_orchestrator.py` continuously monitors execution against safety rules:
1. **Inactivity Timeout**: If no log output is detected for 4 hours (14,400s), the orchestrator automatically aborts the run to prevent hanging processes.
2. **Disk Space Protection**: If storage buffer usage on target VM exceeds 85%, GCE runs are aborted to prevent disk exhaustion.
3. **GKE TPU Memory Management**: Ensure `--use-memory-volumes` flag is enabled in `npi_gke.py` to mount buffers in RAM (`tmpfs`). Skip file cache tests (`read_file_cache`) on TPU slices to avoid host OOM crashes.

### Step 6: Verify BigQuery Metric Export

Upon completion, the orchestrator exports collected metrics to BigQuery tables:
- **Dataset Naming**: Prefix from `targets.json` + `_regional` or `_zonal` (e.g., `npi_gke_orbax_regional`).
- **Tables**: `host_info` (system specs), `fio_<benchmark_name>` (FIO metrics), `go_client_read_<config>` (Go SDK metrics).

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **4-Hour Inactivity Log Stall** | Benchmark execution hung due to deadlocked container or lost SSH connection | Orchestrator auto-aborts. Clean state file (`rm -f ~/.npi/npi_run_state.json`), check SSH socket health, and retrigger. |
| **Buffer Disk Usage > 85%** | FIO write workloads exceeded storage buffer capacity | Abort run. Clean target mount buffer (`/mnt/lssd/*` or `/tmp/npi_buffer/*`), or increase buffer disk size. |
| **GKE TPU Host OOM Crash** | `read_file_cache` test executed on TPU slice using RAM disk buffer | Skip `read_file_cache` tests on TPU slices. Ensure `--use-memory-volumes` is set in `npi_gke.py`. |
| **Corrupted Orchestrator State File** | Invalid JSON formatting in `~/.npi/npi_run_state.json` after process interruption | Execute `rm -f ~/.npi/npi_run_state.json` and relaunch `npi_orchestrator.py`. |
| **BigQuery Export Failure** | Expired GCP credentials or missing BigQuery Data Editor dataset permissions | Refresh credentials (`gcloud auth login`) and grant BigQuery dataset write roles to service account. |

## Verification Checks

1. **Verify BigQuery Table Export**:
   Query the `host_info` and `fio_*` tables to confirm dataset population:
   ```sql
   -- Verify host metadata is recorded
   SELECT run_timestamp, cpu_arch, num_cpus, ram_bytes FROM `<PROJECT_ID>.<BQ_DATASET_ID>.host_info` ORDER BY run_timestamp DESC LIMIT 5;

   -- Verify FIO iteration count matches expected iterations
   SELECT COUNT(*) AS total_records FROM `<PROJECT_ID>.<BQ_DATASET_ID>.fio_read_grpc` WHERE image_version = '<IMAGE_VERSION>';
   ```
2. **Verify Record Count**:
   Ensure `total_records` in BigQuery equals `iterations * target_count * test_matrix_rows`.
3. **Verify Execution Log**:
   Check `npi_commands.log` for zero error exit codes during execution.
