---
name: remediation-advisor
description: Guides on diagnosing GCSFuse NPI performance regressions and conformance test failures, and compiling a structured remediation plan.
---

# GCSFuse NPI Remediation Advisor

This skill guides you through diagnosing regressions, test failures, or resource bottlenecks identified during benchmarking and conformance testing. It provides concrete debugging paths and directs the creation of a structured `npi_remediation_plan.md`.

## Diagnostic Trees

### 1. Performance Regressions (>5% vs Baseline)
- **Check CPU Pinning / NUMA alignment**: Confirm FIO docker runs were pinned to the correct NUMA node matching the network interface (e.g. `numa0`).
- **Check Direct Path (gRPC)**: Verify if Direct Path was active. Check logs for `Direct Path disabled` or `GOAWAY received`. Fallback to HTTP/1.1 or check VPC routing rules.
- **Check Machine Type Configuration**: Validate if the VM family (e.g. `c4`) is registered in `params.yaml` in the GCSFuse repo. If not, GCSFuse defaults to conservative connection limits.

### 2. Conformance Test Failures
- **HNS (Hierarchical Namespace) Mismatch**: Some directory renaming or file operations behave differently if HNS is disabled. Verify if the target GCS bucket has HNS enabled.
- **Permission Errors**: Verify if the VM's service account possesses necessary roles (`Storage Object Admin`, `Storage Legacy Bucket Owner`).
- **Transient Network Failures**: Check for socket timeouts, connections resets, or DNS resolution failures.

### 3. Resource Exhaustion / Hangs
- **Out of Disk (GCE)**: Ensure `/mnt/lssd` (RAID0) was mounted properly and size > 2TiB. If the boot disk filled up instead, increase boot disk size (>=200GB) and verify mounting script.
- **Out of Memory / OOM (GKE TPU)**: Since GKE TPU nodes use RAM disk buffers, ensure `read_file_cache` tests were skipped. Ensure memory limits match node capability.
- **Orchestrator Timeouts (600s)**: If GCSFuse hung, verify the mount options or Direct Path compatibility in that zone.

---

## Step-by-Step Procedure

### Step 1: Analyze Validation Reports and Conformance Results
Review `npi_validation_report.md` and `conformance_results.json` to identify failed test suites or performance regressions.

### Step 2: Formulate Actions based on Diagnostics
Select from the diagnostic trees above to form concrete, actionable steps.

### Step 3: Verify Machine Type Optimization PR
If the machine family is missing from `params.yaml`, the concrete recommendation must include creating a PR in the main GCSFuse repository to whitelist the machine family.

### Step 4: Generate `npi_remediation_plan.md`
Generate a remediation plan with the following structure:

```markdown
# GCSFuse NPI Remediation Plan

## Identified Issues & Gap Analysis
### 1. [Issue Name, e.g., gRPC Read Throughput Regression]
- **Symptom**: [Verbatim error or throughput delta, e.g., gRPC Read BW dropped by 20% compared to baseline]
- **Root Cause Category**: [e.g. Network / Configuration / Code Bug]
- **Diagnostic Details**: [Evidence from logs or BigQuery, e.g., `params.yaml` missing `c4` family]

### 2. [Issue Name, e.g., TestAppendWrite Failure]
- **Symptom**: [Verbatim test failure logs]
- **Diagnostic Details**: [e.g. Bucket lacks Hierarchical Namespace (HNS)]

## Recommended Remediation Steps

### Phase 1: High Priority (Blocking Fixes)
1.  **[Remediation Action 1]**: [Step-by-step resolution details, e.g., Create a PR in GCSFuse repository to add `c4` to the high-performance family list in `params.yaml`]
2.  **[Remediation Action 2]**: [e.g., Enable HNS on the test bucket]

### Phase 2: Medium/Low Priority (Optimizations)
1.  **[Remediation Action 3]**: [e.g., Increase local SSD size to 3TiB or set up RAM buffer with larger limits]

## Verification Plan
[Specify how to verify each fix, e.g. "Rerun performance-benchmarking skill with v3.9.0-patched image and verify through analysis-report-generation that throughput is within 2% of baseline."]
```
