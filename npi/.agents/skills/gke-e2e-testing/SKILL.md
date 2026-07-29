---
name: gke-e2e-testing
description: Guides on provisioning GKE clusters (e.g. n2-standard-64) with Workload Identity and GCSFuse CSI Driver addon enabled, enforcing isolated KUBECONFIG policy, configuring gcs-fuse-csi-driver test suite, executing end-to-end (e2e) tests, and monitoring Ginkgo test execution.
---

# GCSFuse End-to-End (E2E) Testing on GKE

This skill guides you through provisioning GKE compute clusters, configuring strict isolated KUBECONFIG sessions, setting up the GCSFuse CSI Driver test environment, and executing the GCSFuse end-to-end (E2E) test suite using Ginkgo.

---

## Prerequisites & Trigger Conditions

### Prerequisites
1. **GCP Project Access & Permissions**: Local environment configured with `gcloud`, `kubectl`, and `go` (1.24+) CLI tools with permissions to create GKE clusters (`container.clusters.create`), storage buckets (`storage.buckets.create`), and IAM service accounts (`resourcemanager.projects.get`).
2. **KUBECONFIG Isolation Policy**: Standard policy enforcing `mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig` for all cluster creation and `kubectl` operations, ensuring the host default `~/.kube/config` remains completely unmutated.
3. **GCSFuse CSI Driver Repository**: Repository clone of `gcs-fuse-csi-driver` (e.g. at `~/gitproj/gcs-fuse-csi-driver` or local workspace).
4. **Ginkgo Test Framework**: `ginkgo` v2.27.0+ installed (`go install github.com/onsi/ginkgo/v2/ginkgo@v2.27.0`).

### Trigger Conditions
- Triggered when requested to validate functional correctness and E2E integration of GCSFuse on GKE clusters.
- Executed when validating GCSFuse behavior against custom GKE cluster machine types (e.g., `n2-standard-64`, `n2-standard-96`).
- Used to test GCSFuse CSI Driver volume mounting, bucket creation, file caching, kernel list cache, and workload identity integration.

---

## Input/Output Contract

### Inputs
- **`PROJECT_ID`**: GCP Project ID (e.g. `gcs-fuse-test`).
- **`CLUSTER_NAME`**: Name of the target GKE cluster (e.g. `gcsfuse-n2s64-e2e-cluster`).
- **`ZONE_OR_REGION`**: GCP Zone or Region (e.g. `us-central1-c`).
- **`MACHINE_TYPE`**: Worker node machine type (e.g. `n2-standard-64`).
- **`E2E_TEST_USE_GKE_MANAGED_DRIVER`**: Set to `true` when testing against GKE pre-installed GCSFuse CSI driver.
- **`E2E_TEST_GINKGO_PROCS`**: Parallel test worker count (default: `5`).

### Outputs
- **GKE Cluster**: Active GKE cluster with Workload Identity and GCSFuse CSI Driver addon enabled.
- **Kubeconfig Context**: Isolated context configured in `~/.kube/npi_kubeconfig`.
- **E2E Test Execution Logs**: Ginkgo test logs showing test spec status (Pass/Fail/Skip).

---

## Step-by-Step Procedure

### Step 1: Connect to Provided GKE Cluster with Strict KUBECONFIG Isolation

> [!NOTE]
> **Existing Cluster Default**: GKE clusters are typically pre-provisioned or provided in `targets.json` / user request. Do not auto-create a new cluster unless explicitly instructed by the user.

1. Enforce isolated KUBECONFIG environment:
   ```bash
   mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig
   ```

2. Fetch cluster credentials for the target cluster into isolated KUBECONFIG:
   ```bash
   export KUBECONFIG=~/.kube/npi_kubeconfig
   gcloud container clusters get-credentials <CLUSTER_NAME> --project=<PROJECT_ID> --location=<ZONE_OR_REGION>
   ```

3. Verify active context and node readiness:
   ```bash
   kubectl config current-context
   kubectl get nodes -o wide
   ```

### Step 2: On-Demand Cluster Provisioning (Optional - If Explicitly Requested)
If cluster creation is explicitly requested by the user, provision the cluster with GCSFuse CSI Driver and Workload Identity enabled:
```bash
gcloud container clusters create <CLUSTER_NAME> \
    --project=<PROJECT_ID> \
    --location=<ZONE_OR_REGION> \
    --machine-type=<MACHINE_TYPE> \
    --num-nodes=1 \
    --addons=GcsFuseCsiDriver \
    --workload-pool=<PROJECT_ID>.svc.id.goog
```

### Step 3: Configure Environment for GCSFuse E2E Suite

Navigate to the `gcs-fuse-csi-driver` repository directory:
```bash
cd /path/to/gcs-fuse-csi-driver
```

Set necessary environment variables:
```bash
export KUBECONFIG=~/.kube/npi_kubeconfig
export E2E_TEST_USE_GKE_MANAGED_DRIVER=true
export E2E_TEST_GINKGO_PROCS=5
```

### Step 4: Execute GCSFuse E2E Test Suite

Launch the complete E2E test suite using `make`:
```bash
make e2e-test
```

Alternatively, to run selective test suites focusing specifically on FUSE kernel behavior (kernel parameters, dentry cache, readdirplus, negative stat cache, read cache, process interrupts, and volume mounting) with an extended timeout:
```bash
export KUBECONFIG=~/.kube/npi_kubeconfig
export E2E_TEST_USE_GKE_MANAGED_DRIVER=true
export ENABLE_GCSFUSE_KERNEL_PARAMS=true
export E2E_TEST_GINKGO_TIMEOUT=8h
export E2E_TEST_FOCUS="kernelParams|kernel_list_cache|dentry_cache|readdirplus|negative_stat_cache|read_cache|buffered_read|interrupt|stale_handle|local_file|streaming_writes|concurrent_operations|operations|file_cache|rename_symlink|volumes|mount"
export E2E_TEST_SKIP="multivolume|list_large_dir|should.succeed.in.performance.test|oidc"
export E2E_TEST_GINKGO_PROCS=5
make e2e-test
```

To skip stress/scale tests (`list_large_dir`), multi-node tests (`multivolume` on single-node clusters), or long-running performance suites:
```bash
export E2E_TEST_SKIP="multivolume|list_large_dir|should.succeed.in.performance.test|oidc"
make e2e-test
```

### Step 5: Clean Up Resources Post-Test

Once E2E testing completes, delete test resources and cluster if temporary:
```bash
export KUBECONFIG=~/.kube/npi_kubeconfig
gcloud container clusters delete <CLUSTER_NAME> --project=<PROJECT_ID> --location=<ZONE_OR_REGION> --quiet
```

---

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Recovery / Remediation Action |
|---|---|---|
| **Host KUBECONFIG Overwritten** | `gcloud container clusters get-credentials` executed without setting `KUBECONFIG` | Always execute `mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig` before running any `gcloud` or `kubectl` commands. |
| **CSI Driver Pod Crash** | GCSFuse CSI driver addon disabled on cluster | Verify CSI driver status: `kubectl get pods -n kube-system -l app=gcs-fuse-csi-driver`. If missing, update cluster: `gcloud container clusters update <CLUSTER_NAME> --update-addons GcsFuseCsiDriver=ENABLED`. |
| **Ginkgo Parallel Lock Contention** | High process count (`E2E_TEST_GINKGO_PROCS`) causing API server rate limiting | Reduce process count: `export E2E_TEST_GINKGO_PROCS=2` or `5`. |
| **Stale Test Namespaces / Buckets** | Test aborted mid-run leaving lingering test namespaces (`gcsfuse-integration-*`) | Run manual cleanup: `kubectl get ns | grep gcsfuse-integration | awk '{print $1}' | xargs -r kubectl delete ns`. |

---

## Verification Checks

1. **Cluster Context Verification**:
   ```bash
   kubectl config current-context | grep "<CLUSTER_NAME>"
   ```
2. **CSI Driver DaemonSet Check**:
   ```bash
   kubectl get ds gcs-fuse-csi-driver-node -n kube-system
   ```
3. **Ginkgo Test Result Check**:
   Verify test output ends with `PASS` and summary metrics showing zero unexpected failures.
