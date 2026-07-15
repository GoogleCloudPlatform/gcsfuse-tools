---
name: conformance-testing
description: Guides on cloning GCSFuse, executing Makefile npi-conformance target on target GCE VMs, parsing integration_tests.log into conformance_results_<TARGET_NAME>.json, enforcing non-blocking permission error policies, handling 5-minute log stall timeouts via pkill and umount cleanup, and skipping unsupported GKE cluster environments.
---

# GCSFuse Conformance and Integration Testing

This skill guides you through checking out the official GCSFuse repository, executing integration and POSIX conformance test suites on a target GCE VM using the standardized `make npi-conformance` target, parsing test output logs, and generating `conformance_results_<TARGET_NAME>.json`.

> [!IMPORTANT]
> **GCE VM Targets Only**: Conformance and integration testing (`go test` execution) is supported ONLY on GCE VM targets. For GKE cluster targets, conformance testing is skipped because GKE validation relies on GKE performance benchmark workloads. Do not attempt to run conformance tests on GKE nodes.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **Go Language Environment**: Go installed on target GCE VM matching GCSFuse `go.mod` (typically Go 1.22+).
2. **GCP Storage Credentials**: Target GCE VM service account configured with permissions to read/write test GCS buckets (`storage-rw` scope or `Storage Object Admin` role).
3. **Active Master SSH Connection**: Established SSH connection socket at `~/.ssh/sockets/<TARGET_NAME>.sock`.
4. **Target GCS Bucket**: Dedicated test bucket for integration test file operations.
5. **KUBECONFIG Isolation Policy**: Standard policy enforcing `mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig` for any cluster interactions, ensuring host default `~/.kube/config` remains unmutated.

### Trigger Conditions
- Validating POSIX compatibility and functional correctness of GCSFuse on a target GCE VM platform.
- Executed during NPI qualification prior to or alongside performance benchmarking suites.
- Triggered when testing new GCSFuse code releases or feature branches on GCE VM targets.

## Input/Output Contract

### Inputs
- **Target SSH Socket**: Socket path `~/.ssh/sockets/<TARGET_NAME>.sock`.
- **Target GCE VM Specs**: VM Name, Zone, GCP Project ID, SSH User.
- **Makefile Parameters**: `PROJECT=<PROJECT_ID>`, `BUCKET_LOCATION=<REGION>`, `READ_AHEAD_KB=<KB>` (defaults to 128).
- **GCSFuse Version / Branch**: Git commit tag or branch name (`<GCSFUSE_VERSION_OR_BRANCH>`).

### Outputs
- **Remote Log**: `~/integration_tests.log` generated on target VM.
- **Local Deliverable**: `conformance_results_<TARGET_NAME>.json` containing:
  - ISO 8601 Timestamp, GCSFuse version, target VM name.
  - Summary metrics: `total_tests`, `passed`, `failed`, `skipped`.
  - Detailed list of individual test cases, pass/fail status, execution duration, and error strings.

## Step-by-Step Procedure

### Step 1: Clone the GCSFuse Repository on Target VM

Connect to the target VM using the master SSH socket and clone GCSFuse:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash -s" << 'EOF'
  git clone https://github.com/GoogleCloudPlatform/gcsfuse.git ~/gcsfuse
  cd ~/gcsfuse
  git checkout <GCSFUSE_VERSION_OR_BRANCH>
EOF
```

### Step 2: Prepare Test Bucket and Config

Verify environmental parameters on the target VM (e.g., `GCSFUSE_TEST_BUCKET`).

### Step 3: Run Integration Tests via `make npi-conformance`

> [!IMPORTANT]
> **Makefile Integration**:
> GCSFuse features a dedicated Makefile target `npi-conformance` that:
> 1. Excludes emulator tests.
> 2. Runs dual-configuration execution (Phase 1: Without Read-Ahead, Phase 2: With Read-Ahead).
> 3. Resolves project and bucket parameters dynamically.
> 4. Executes test packages sequentially to avoid resource contention.
>
> Always invoke this Makefile target instead of running manual `go test` commands.

Execute the conformance test suite remotely using `run_conformance.sh` (which incorporates an automated log size watchdog loop that monitors `~/integration_tests.log` for 5-minute inactivity stalls, terminates deadlocked processes, and cleans up FUSE mounts automatically):
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash -s" << 'EOF'
  PROJECT=<PROJECT_ID> BUCKET_LOCATION=<REGION> READ_AHEAD_KB=128 bash ~/gcsfuse-tools/npi/run_conformance.sh
EOF
```

### Step 4: Parse Log and Generate `conformance_results_<TARGET_NAME>.json`

Parse `~/integration_tests.log` on target VM and export structured JSON:

Example JSON structure:
```json
{
  "timestamp": "2026-06-14T15:29:19Z",
  "gcsfuse_version": "<GCSFUSE_VERSION_OR_BRANCH>",
  "target_vm": "<VM_NAME>",
  "summary": {
    "total_tests": 120,
    "passed": 118,
    "failed": 2,
    "skipped": 0
  },
  "tests": [
    {
      "name": "TestReadOperations/BasicRead",
      "status": "PASS",
      "duration_seconds": 1.45
    },
    {
      "name": "TestWriteOperations/AppendWrite",
      "status": "FAIL",
      "duration_seconds": 3.12,
      "error": "write error: connection reset by peer"
    }
  ]
}
```

Copy the generated JSON report to local machine:
```bash
scp -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com:~/conformance_results.json ./conformance_results_<TARGET_NAME>.json
```

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **Target is GKE Cluster** | Conformance testing attempted on GKE node | Skip conformance testing on GKE targets. Document GKE skip policy in report and rely on GKE benchmark runs. |
| **5-Minute Log Stall / Process Hang** | `go test` or GCSFuse daemon deadlocked during test execution | Check remote log `~/integration_tests.log` size every 5 mins. If size is unchanged for >5 mins: <br> 1. Terminate processes: `ssh ... "sudo pkill -9 -f 'go test' ; sudo pkill -9 gcsfuse ; sudo pkill -9 -f proxy_server"` <br> 2. Force unmount leftover mounts: `ssh ... "sudo umount -f /tmp/gcsfuse_readwrite_test_*/mnt || true"` <br> 3. Clean temp directories: `ssh ... "sudo rm -rf /tmp/gcsfuse_*"` <br> 4. Record stall in JSON. |
| **Permission Failure Tests** | Test asserts bucket operations restricted by service account permissions | Non-blocking policy. Do NOT abort pipeline. Parse and record test failures in `conformance_results_<TARGET_NAME>.json` and document in final report. |
| **Go Version Mismatch** | Target GCE VM missing Go 1.22+ runtime | Install Go 1.22+ on target VM or set `PATH` to system Go binary before running Makefile. |

## Verification Checks

1. **Verify Deliverable JSON File**:
   Confirm local output file exists and contains valid JSON:
   ```bash
   jq .summary ./conformance_results_<TARGET_NAME>.json
   ```
2. **Verify Summary Counts**:
   Ensure `total_tests` > 0 and `passed + failed + skipped == total_tests`.
3. **Verify Clean Exit / No Lingering Mounts**:
   Check target VM for leftover FUSE test mountpoints:
   ```bash
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "mount | grep gcsfuse_readwrite_test || echo 'NO_LEFTOVER_MOUNTS'"
   ```
