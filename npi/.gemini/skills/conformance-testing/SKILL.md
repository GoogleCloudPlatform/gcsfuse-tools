---
name: conformance-testing
description: Guides on running GCSFuse integration/conformance tests on target nodes.
---

# GCSFuse Conformance and Integration Testing

This skill guides you through checking out the official GCSFuse repository, executing the integration and conformance test suites on a target GCE VM, and parsing the test outputs into a structured `conformance_results.json`.

> [!IMPORTANT]
> **GCE VM Targets Only**: Conformance and integration testing (`go test` execution) is only supported and executed on GCE VM targets. For GKE cluster target environments, conformance testing is skipped (GKE validation relies on GKE performance benchmark runs instead). Do not attempt to run conformance tests on GKE nodes.

## Prerequisites

1.  **Go Language Environment**: Ensure Go is installed on the target machine (conforming to the version specified in the GCSFuse `go.mod` file, typically Go 1.22+).
2.  **GCP Authentication / Credentials**: The VM must have appropriate access scopes or service account credentials to read/write to the test GCS buckets (e.g. `storage-rw`).
3.  **Active SSH Socket**: Use the persistent SSH connection established to the target VM/node. If establishing a new connection:
    ```bash
    # Check and remove stale socket files if present before starting master connection
    rm -f ~/.ssh/sockets/<TARGET_NAME>.sock

    ssh -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com
    ```

## Step-by-Step Procedure

### Step 1: Clone the GCSFuse Repository on the Target VM

Connect to the target VM using the persistent SSH socket and clone the repository:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash -s" << 'EOF'
  git clone https://github.com/GoogleCloudPlatform/gcsfuse.git ~/gcsfuse
  cd ~/gcsfuse
  git checkout <GCSFUSE_VERSION_OR_BRANCH>
EOF
```

### Step 2: Prepare the Test Bucket and Config

Ensure the target bucket is prepared and verify the project configuration:
- Standard integration tests require a bucket.
- Check if you have the permission to run tests. Some tests require specific environment variables (e.g., `GCSFUSE_TEST_BUCKET`).

### Step 3: Run the Integration Tests

Navigate to the cloned repository and run the integration tests:

> [!IMPORTANT]
> **Mandatory Go Flags**: In newer versions of GCSFuse, the integration tests will be silently skipped unless you explicitly pass the `--integrationTest` flag. You must also specify the target bucket using the `--testbucket=<bucket_name>` flag. Without these, the test suite will run zero tests and report a misleading `PASS` status in less than a minute.
> It is also highly recommended to run the packages sequentially using the `-p 1` flag to avoid concurrent mounting conflicts on the same target VM, and set a long timeout (e.g., `-timeout=60m`).

```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash -s" << 'EOF'
  cd ~/gcsfuse
  # Run all integration tests under tools/integration_tests, excluding emulator_tests
  # Pass both the --integrationTest and --testbucket flags.
  go test -p 1 -v $(go list ./tools/integration_tests/... | grep -v emulator_tests) --integrationTest --testbucket=<TEST_BUCKET_NAME> -timeout=60m > ~/integration_tests.log 2>&1
EOF
```

### Step 4: Parse Results and Generate `conformance_results.json`

Extract the status of the tests and generate a structured JSON report `conformance_results.json`.
You can use a local parsing script or a python invocation on the target machine to parse `integration_tests.log` and output `conformance_results.json`.

Example structure of `conformance_results.json`:
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

Copy the generated JSON report back to the orchestrator environment, naming it uniquely per target to prevent overwrites:
```bash
scp -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com:~/conformance_results.json ./conformance_results_<TARGET_NAME>.json
```

### Step 5: Analyze and Document Failures (Do Not Block on Permissions)

Some conformance or integration tests might fail due to environmental limitations or intentional credential restrictions (e.g., tests asserting read-only access where permissions are restricted).
- **Do not abort the pipeline**: Do not halt the run or block the pipeline trying to resolve permission failures or make 100% of the tests pass.
- **Extract Failure Reasons**: Parse the test logs to identify the exact cause (e.g., "PermissionDenied: service account lacks storage.buckets.get").
- **Document in Deliverables**: Ensure all failed tests, error logs, and root causes are correctly outputted to `conformance_results.json`. They must be detailed in the final `npi_validation_report.md` for review.
- **Monitor for Stalls**: Monitor the log progress (`~/integration_tests.log` size) on the remote VM at regular check-in intervals (e.g., every 5 minutes). If the log file size remains unchanged for more than 5 minutes while the `go test` process is active, it indicates a test stall/hang. In this event, you must immediately:
  1. Kill all test and GCSFuse daemon processes: `sudo pkill -9 -f 'go test' ; sudo pkill -9 gcsfuse ; sudo pkill -9 -f proxy_server`
  2. Force-unmount any leftover test mounts: `sudo umount -f /tmp/gcsfuse_readwrite_test_*/mnt || true`
  3. Clean up the temp directories to free up inodes: `sudo rm -rf /tmp/gcsfuse_readwrite_test_* /tmp/gcsfuse_integration_tests*`
  4. Document the stall and process dump details in `conformance_results.json` and the final report.
