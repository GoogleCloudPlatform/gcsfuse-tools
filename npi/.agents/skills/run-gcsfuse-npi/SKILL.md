---
name: run-gcsfuse-npi
description: Master entrypoint and orchestration skill for running the end-to-end GCSFuse New Product Introduction (NPI) pipeline across modular skills, coordinating SSH socket setup, bucket creation via bucket-creation skill, target buffer mounting via raid0-script.sh, image building via build_images.py, POSIX & E2E conformance testing across all targets (GCE VMs via make npi-conformance and GKE clusters via gke-e2e-testing), benchmark suite execution via npi_orchestrator.py, analysis and validation report generation in npi_validation_report.md, and remediation planning in npi_remediation_plan.md.
---

# GCSFuse NPI Master Orchestration Entrypoint

This skill serves as the primary master entrypoint for executing and orchestrating the complete end-to-end GCSFuse New Product Introduction (NPI) validation, benchmarking, POSIX & E2E conformance testing, analysis, and remediation pipeline across all modular skills.

---

## Prerequisites & Trigger Conditions

### Prerequisites
1. **GCP Project Access & Credentials**: Local environment configured with `gcloud`, `kubectl`, and `bq` CLI tools with permissions to create storage resources, push container images, run GKE workloads, and write to BigQuery. All `gcloud container clusters get-credentials` and `kubectl` operations MUST use strict KUBECONFIG isolation (`mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig`) to ensure the host default `~/.kube/config` is never mutated or overwritten.
2. **GCSFuse Source Checkout**: Local repository clone of GCSFuse.
3. **Target Specifications (`targets.json`)**: Dynamic target configuration template (`targets.json`) defining target GCE VMs, GKE clusters, storage buffer paths, node selectors, and GCP buckets. For GKE cluster targets, SSD availability (`has_ssd`) MUST be verified on the GKE cluster worker nodes (e.g. via `gcloud container node-pools describe` or `kubectl get nodes`), NOT on the intermediate controller runner VM.
4. **SSH Access**: Configured SSH key at `~/.ssh/google_compute_engine` for connecting to target GCE VMs and GKE intermediate controller runner VMs.
5. **GKE Cluster Addons**: For GKE targets, the cluster MUST have Workload Identity enabled (`gcloud container clusters update <CLUSTER> --workload-pool=<PROJECT_ID>.svc.id.goog`) and the GCSFuse CSI driver addon enabled (`gcloud container clusters update <CLUSTER> --update-addons GcsFuseCsiDriver=ENABLED`). Node pools must have Workload Metadata enabled (`gcloud container node-pools update <POOL> --cluster=<CLUSTER> --workload-metadata=GKE_METADATA`).

### Trigger Conditions
- Initiating end-to-end GCSFuse NPI qualification for a new software release or platform target.
- Dispatched when requested to execute full pipeline: SSH Connection -> Storage Bucket Provisioning -> Target Setup -> Conformance Testing -> Benchmark Suite Execution -> Analysis & Validation Report -> Remediation Advisory.

---

## Input/Output Contract

### Inputs
- **`targets.json`**: Target configurations schema defining target names, VM names (`vm_name` specifies the GCE VM or the GKE controller runner VM), zones, bucket names, BigQuery dataset prefixes, buffer mount paths, and machine configurations.
- **Workflow Parameters**: Image tag version (`<IMAGE_VERSION>`), GCSFuse version tag (`<GCSFUSE_VERSION>`, default: `master`), iteration count, benchmark selection (`read_http1`, `read_grpc`, `write_http1`, `write_grpc`, `read_file_cache`, `all`), and optional smoke mode flag (`--smoke-mode`).
- **Baseline Dataset ID** (Optional): Historical BigQuery dataset ID for regression comparison.

### Outputs
- **Lifecycle Artifacts**:
  1. Active master SSH sockets at `~/.ssh/sockets/<TARGET_NAME>.sock` (via `ssh-connection-management`).
  2. Provisioned Regional or Zonal RAPID GCS buckets with HNS enabled (via `bucket-creation`).
  3. Mounted target storage buffers (RAID0 or `tmpfs` RAM disk) and pushed container image `us-docker.pkg.dev/<PROJECT_ID>/gcsfuse-npi-images:<IMAGE_VERSION>` (via `benchmark-build-setup`).
  4. `conformance_results_<TARGET_NAME>.json` for all target environments (GCE VMs via `conformance-testing`, GKE clusters via `gke-e2e-testing`).
  5. BigQuery benchmark datasets (`<prefix>_regional` or `_zonal`) containing `host_info` and `fio_*` metrics (via `benchmark-suite-execution`).
  6. `npi_validation_report.md` with explicit PASS/FAIL verdict for the 20 GB/s non-pinned SLA gate (via `analysis-report-generation`).
  7. `npi_remediation_plan.md` outlining tuning recommendations if SLA gate fails or regressions >5% occur (via `remediation-advisor`).

---

## Step-by-Step Procedure

The end-to-end pipeline executes sequentially through modular phases:

```
+-----------------------------------------------------------------------------------+
| Phase 1: SSH Connection Management                                               |
| Establish persistent SSH master multiplexing sockets for all targets             |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Phase 1.5: Storage Bucket Provisioning                                           |
| Provision Regional HNS or Zonal RAPID HNS GCS buckets via `bucket-creation`      |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Phase 2: Benchmark Build & Target Setup                                          |
| Mount RAID0 / tmpfs RAM disks, configure Docker, build & push container images   |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Phase 3: POSIX & E2E Conformance Testing                                         |
| Execute conformance tests across all targets (GCE VMs & GKE) & export JSON results|
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Phase 4: Benchmark Suite Execution                                                |
| Execute `npi_orchestrator.py` across targets, monitor safety rules, export to BQ|
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Phase 5: Analysis & Validation Report Generation                                  |
| Query BQ, verify 20 GB/s non-pinned SLA gate, generate `npi_validation_report.md` |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Phase 6: Remediation Advisory (Conditional)                                      |
| If SLA gate fails or regressions >5% occur, compile `npi_remediation_plan.md`    |
+-----------------------------------------------------------------------------------+
```

### Phase 1: Establish Persistent SSH Connections
*Skill Reference*: **[SSH Connection Management](../ssh-connection-management/SKILL.md)**
1. Create socket directory `mkdir -p ~/.ssh/sockets`.
2. Clean stale socket files after verifying liveness check.
3. Establish master connections for each target in `targets.json` (for GKE targets, `vm_name` specifies the controller runner VM):
   ```bash
   SSH_USER="${SSH_USER:-$(gcloud config get-value account 2>/dev/null | tr '@.' '_')}"
   ssh -f -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com
   ```
4. Verify connection liveness with `ssh -O check -S ~/.ssh/sockets/<TARGET_NAME>.sock`.

### Phase 1.5: Storage Bucket Provisioning
*Skill Reference*: **[Bucket Creation](../bucket-creation/SKILL.md)**
1. For targets requiring regional standard storage, provision Regional HNS buckets:
   ```bash
   gcloud storage buckets create gs://<BUCKET_NAME> --project=<PROJECT_ID> --location=<REGION> --enable-hierarchical-namespace --uniform-bucket-level-access
   ```
2. For targets requiring zonal RAPID storage, provision Zonal RAPID HNS buckets:
   ```bash
   gcloud storage buckets create gs://<BUCKET_NAME> --project=<PROJECT_ID> --location=<REGION> --placement=<ZONE> --default-storage-class=RAPID --enable-hierarchical-namespace --uniform-bucket-level-access
   ```
3. Describe bucket properties using `gcloud storage buckets describe gs://<BUCKET_NAME> --project=<PROJECT_ID> --format="json"` to confirm HNS and location alignment.

### Phase 2: Target Buffer Setup & Image Build
*Skill Reference*: **[Benchmark Build & Setup](../benchmark-build-setup/SKILL.md)**
1. Check if target buffer is already mounted (`mountpoint -q <SSD_MOUNT_PATH>`). If not, execute `raid0-script.sh` on target.
2. Install Docker, add user to `docker` group (`usermod -aG docker`).
3. **CRITICAL**: Recreate SSH multiplexing socket (`rm -f ~/.ssh/sockets/<TARGET_NAME>.sock` + relaunch Phase 1 master command) to apply docker group session changes.
4. Configure Artifact Registry credentials locally and remotely (`gcloud auth configure-docker us-docker.pkg.dev`).
5. Execute image build script (default version parameter is `master`):
   ```bash
   python3 build_images.py --project <PROJECT_ID> --image-version <IMAGE_VERSION> --gcsfuse-version master [--smoke-mode]
   ```
6. Restore matrix files if smoke-test matrices were edited (`git restore fio/read_matrix.csv fio/write_matrix.csv`).

### Phase 3: POSIX & E2E Conformance Testing
*Skill Reference*: **[Conformance Testing](../conformance-testing/SKILL.md)** for GCE VMs, **[GKE E2E Testing](../gke-e2e-testing/SKILL.md)** for GKE Clusters

> [!IMPORTANT]
> **Mandatory Execution Policy**: Conformance and integration testing is mandatory for ALL targets (both GCE VMs and GKE clusters) and must ALWAYS be executed by default during NPI qualification. Do NOT skip conformance testing unless the user explicitly requests to exclude it.

1. **For GKE Targets**:
   - Enforce isolated KUBECONFIG: `mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig`.
   - Connect to target cluster and execute GCSFuse CSI Driver E2E integration test suite (`make e2e-test` via `gke-e2e-testing` skill).
   - Parse Ginkgo test outputs into `./conformance_results_<TARGET_NAME>.json`.
2. **For GCE VM Targets**:
   - Clone GCSFuse repo on target VM (`~/gcsfuse`).
   - Execute standardized Makefile target (default branch `master`):
     ```bash
     SSH_USER="${SSH_USER:-$(gcloud config get-value account 2>/dev/null | tr '@.' '_')}"
     ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "cd ~/gcsfuse && make npi-conformance PROJECT=<PROJECT_ID> BUCKET_LOCATION=<REGION> READ_AHEAD_KB=128 GCSFUSE_VERSION=master > ~/integration_tests.log 2>&1"
     ```
   - Monitor remote log growth. If log size stalls for >5 minutes, kill processes (`pkill -9`), force unmount (`umount -f`), clean temp files, and record stall.
   - Parse `~/integration_tests.log` and copy `conformance_results_<TARGET_NAME>.json` back to local orchestrator using `scp -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine`. Enforce non-blocking policy on permission failures.

### Phase 4: Benchmark Suite Execution
*Skill Reference*: **[Benchmark Suite Execution](../benchmark-suite-execution/SKILL.md)**
1. Verify host-level OS tuning (LRO/GRO offloads, RFS/RPS packet steering) on target VMs.
2. For fresh runs, clean run state file: `rm -f ~/.npi/npi_run_state.json`.
3. Enforce strict KUBECONFIG isolation (`mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig`) prior to launching GKE cluster credentials fetching or `kubectl` commands.
4. Launch benchmark orchestrator (adding `--smoke-mode` if running fast smoke test mode):
   ```bash
   python3 npi_orchestrator.py --benchmarks "<BENCHMARK_LIST>" --image-version <IMAGE_VERSION> --iterations <ITERATION_COUNT> [--smoke-mode]
   ```
5. Active monitoring during run: enforce 4-hour inactivity log timeout, 85% buffer disk space limit, and TPU memory volume RAM disk flags (`--use-memory-volumes`).
6. Exclude `read_file_cache` when target is a GKE TPU node pool (`is_tpu: true` or `has_ssd: false`).
7. Pre-truncate BigQuery dataset tables ONCE before launching parallel target worker threads to prevent concurrency truncation races.

### Phase 5: Analysis & Validation Report Generation
*Skill Reference*: **[Analysis & Report Generation](../analysis-report-generation/SKILL.md)**
1. Query host metadata from BigQuery `<DATASET_PREFIX>_regional.host_info`.
2. Query performance metrics from `<DATASET_PREFIX>_regional.fio_*` using quoted JSON keys (`JSON_VALUE(fio_json_output, '$."fio version"')`).
3. Evaluate baseline comparisons (if baseline dataset available) and intra-run comparisons (gRPC vs HTTP/1.1, NUMA vs non-NUMA).
4. Evaluate strict **20 GB/s SLA Gate**: 1G file size, 1M block size, 128 numjobs, 10 files sequential reads without caches in **standard, non-NUMA-pinned runs**. Mark as **FAIL / REJECTED** if non-pinned throughput < 20 GB/s in full runs, or mark as `SKIPPED (Smoke Test Run - Scaled Parameters)` when running under smoke mode.
5. Inspect `params.yaml` for machine type classification (`c4-standard-96`).
6. Compile findings into `npi_validation_report.md`.

### Phase 6: Remediation Advisory Plan (Conditional)
*Skill Reference*: **[Remediation Advisor](../remediation-advisor/SKILL.md)**
1. If SLA gate fails (< 20 GB/s non-pinned), performance regresses >5%, or conformance tests fail:
   - Analyze root causes across FUSE queue depths (`--max-background=512`), gRPC connection pools (`--experimental-grpc-conn-pool-size=128`), HTTP max connections, OS LRO/GRO offloads (`ethtool`), and RFS/RPS packet steering (`sysctl`).
2. Generate structured advisory document `npi_remediation_plan.md`. Enforce Advisory-Only policy (do NOT auto-execute remediation commands).

---

## Failure Modes & Edge Cases

| Pipeline Phase | Failure Scenario | Detection Criteria | Recovery / Remediation Action |
|---|---|---|---|
| **Phase 1: SSH** | Stale control socket | `Control socket connect failed: Connection refused` | Run `rm -f ~/.ssh/sockets/*.sock` and re-establish SSH master connection. |
| **Phase 2: Setup** | Docker permission denied | `permission denied while trying to connect to Docker daemon` | Close socket (`rm -f ~/.ssh/sockets/<TARGET_NAME>.sock`) and recreate master SSH connection to refresh user group IDs. |
| **Phase 2: Setup** | No local SSDs on VM | `has_ssd: false` or `lsblk` shows no NVMe SSDs | `raid0-script.sh` falls back automatically to verify >=550GB RAM and mount 500GB `tmpfs` RAM disk. |
| **Phase 3: Conformance** | 5-Minute Log Inactivity Stall | `~/integration_tests.log` file size unchanged for >5 min | Execute process kill (`pkill -9 -f 'go test'`), force unmount (`umount -f`), clean `/tmp/gcsfuse_*`, and document stall in JSON deliverable. |
| **Phase 3: Conformance** | Permission Test Failures | `PermissionDenied` error in integration log | Non-blocking policy: do NOT halt pipeline. Log errors in JSON deliverable and continue to Phase 4. |
| **Phase 4: Benchmark** | 4-Hour Inactivity Timeout | Orchestrator log shows 14,400s without output | Abort run, clean state file (`rm -f ~/.npi/npi_run_state.json`), check socket connection, and retrigger. |
| **Phase 4: Benchmark** | Disk Buffer Usage > 85% | Storage buffer disk usage exceeds 85% threshold | Abort GCE runs, purge test output directory `/mnt/lssd/*` or increase buffer disk size. |
| **Phase 5: Analysis** | Non-pinned Throughput < 20 GB/s | Non-pinned 1G sequential read throughput < 20 GB/s | Mark overall NPI validation as **FAIL / REJECTED** in Executive Summary. Auto-trigger Phase 6 Remediation Advisor. |
| **Phase 6: Remediation** | Unrequested System Edit | Attempting to run `sysctl` or `ethtool` on remote host | Enforce Advisory-Only policy. Never auto-execute system configuration changes. Output recommended commands to `npi_remediation_plan.md`. |

---

## Verification Checks

Verify complete end-to-end pipeline deliverables upon completion:

1. **Phase 1 Socket Check**:
   ```bash
   ls -la ~/.ssh/sockets/
   ```
2. **Phase 2 Image Check**:
   ```bash
   gcloud artifacts docker images list us-docker.pkg.dev/<PROJECT_ID>/gcsfuse-npi-images --image-format='value(format("{0}:{1}",package,tag))' | grep "<IMAGE_VERSION>"
   ```
3. **Phase 3 Conformance JSON Check** (for all targets):
   ```bash
   test -s ./conformance_results_<TARGET_NAME>.json && jq .summary ./conformance_results_<TARGET_NAME>.json
   ```
4. **Phase 4 BigQuery Metrics Check**:
   ```sql
   SELECT run_timestamp, cpu_arch, num_cpus FROM `<PROJECT_ID>.<BQ_DATASET_ID>.host_info` LIMIT 5;
   ```
5. **Phase 5 Validation Report Check**:
   ```bash
   test -s npi_validation_report.md && grep -E "(PASS|FAIL|REJECTED)" npi_validation_report.md
   ```
6. **Phase 6 Remediation Plan Check** (if SLA failed or regressions detected):
   ```bash
   test -s npi_remediation_plan.md && grep -E "(Identified Issues|Phase 1|Phase 2)" npi_remediation_plan.md
   ```
