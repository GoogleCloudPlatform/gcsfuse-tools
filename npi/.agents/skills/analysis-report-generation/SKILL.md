---
name: analysis-report-generation
description: Guides on querying benchmark results from BigQuery tables via bq query or query_results.py, evaluating throughput and latency metrics against baselines or intra-run configurations, separating sequential and random read workloads, verifying params.yaml machine type classification, assessing the strict 20 GB/s SLA gate on non-pinned runs, and generating the structured npi_validation_report.md deliverable while handling JSON key spacing errors and missing baseline fallbacks.
---

# GCSFuse NPI Analysis & Report Generation

This skill guides you through querying benchmark results from BigQuery tables, performing analysis on throughput and latency trends against historical baselines, verifying machine type configuration optimizations in `params.yaml`, evaluating the strict 20 GB/s SLA performance gate, and compiling the findings into a standardized `npi_validation_report.md`.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **GCP / BigQuery Access**: The environment must have access rights and `bq` CLI credentials to query the BigQuery datasets containing benchmark run outputs.
2. **Baselines Datasets (Optional)**: Access to baseline dataset IDs (e.g., `npi_benchmarks_baseline_lro_on`) if performing historical baseline comparisons. If baselines are unavailable, intra-run comparative analysis is required.
3. **GCSFuse Source Code**: Access to the local GCSFuse repository checkout to inspect `params.yaml` for machine type classification.

### Trigger Conditions
- Benchmark execution (`npi_orchestrator.py` or `npi_gke.py`) has completed and metrics are exported to BigQuery.
- Requesting an official validation report (`npi_validation_report.md`) for GCSFuse NPI release or platform qualification.
- Evaluating whether a target machine type and protocol meet the strict 20 GB/s SLA gate requirements.

## Input/Output Contract

### Inputs
- **BigQuery Datasets & Tables**:
  - `<PROJECT_ID>.<DATASET_ID>.host_info` (System specifications and hardware profile)
  - `<PROJECT_ID>.<DATASET_ID>.fio_<benchmark>` (e.g., `fio_read_grpc`, `fio_read_http1`, `fio_write_grpc`, `fio_write_http1`)
  - `<PROJECT_ID>.<DATASET_ID>.go_client_read_<config>` (Go SDK client benchmark metrics)
- **Baseline Dataset ID** (Optional): Historical BigQuery dataset for regression comparison.
- **`params.yaml`**: GCSFuse repository file located at `params.yaml` for machine type verification.
- **Target Metadata**: `targets.json` specifying platform type (GCE VM vs GKE Cluster) and target names.

### Outputs
- **`npi_validation_report.md`**: Main validation report containing:
  - Executive Summary with explicit **PASS / FAIL / REJECTED** verdict for the 20 GB/s SLA gate.
  - System Specifications & Hardware Profiles table populated from `host_info`.
  - Dedicated **Sequential Read Performance (`read`)** breakdown tables.
  - Dedicated **Random Read Performance (`randread`)** breakdown tables.
  - Dedicated **Streaming Write Performance (`write`)** breakdown tables.
  - Protocol comparison tables (HTTP/1.1 vs gRPC) and Baseline comparisons.
  - Machine Type Classification Status and PR Action items.
  - Failure Observations & Issue Log.

## Step-by-Step Procedure

### Step 1: Query BigQuery Results

Retrieve system hardware metadata and performance metrics from the respective BigQuery tables.

#### 1. Query Host Specifications
Run the following SQL query against the `host_info` table to retrieve system hardware specs:
```sql
SELECT
  run_timestamp,
  cpu_arch,
  num_cpus,
  num_numa_nodes,
  kernel_version,
  ram_bytes,
  num_local_ssds
FROM
  `<PROJECT_ID>.<DATASET_ID>.host_info`
ORDER BY run_timestamp DESC
LIMIT 1
```

#### 2. Query Performance Metrics (FIO JSON Handling & Read Type Disaggregation)
> [!IMPORTANT]
> **Read Type Disaggregation**: In FIO JSON output, the workload read mode is stored in `fio_json_output.global_options.rw` (with space in key). Always query it using `JSON_VALUE(fio_json_output, '$."global options".rw')` to distinguish between Sequential Reads (`read`) and Random Reads (`randread`). Never aggregate `read` and `randread` together.

Execute the performance query via the `bq` CLI tool:
```bash
bq query --project_id=<PROJECT_ID> --use_legacy_sql=false \
"SELECT
  _TABLE_SUFFIX AS protocol,
  JSON_VALUE(fio_json_output, '\$.\"global options\".rw') AS read_type,
  JSON_VALUE(job, '\$.\"job options\".bs') AS block_size,
  JSON_VALUE(job, '\$.\"job options\".filesize') AS file_size,
  ROUND(AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) * 1024.0 / 1000000.0, 2) AS read_bw_mbs,
  ROUND(AVG(SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64)) / 1000000.0, 2) AS read_lat_ms,
  ROUND(AVG(SAFE_CAST(JSON_VALUE(job.read.iops) AS FLOAT64)), 2) AS read_iops
FROM
  \`<PROJECT_ID>.<DATASET_ID>.fio_read_*\`,
  UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
GROUP BY 1, 2, 3, 4
ORDER BY protocol, read_type, block_size, file_size"
```

### Step 2: Compare Against Baselines & Perform Intra-Run Analysis

#### 1. Baseline Performance Comparison
If a baseline BigQuery dataset is available, calculate the percentage throughput and latency deltas (`(New - Baseline) / Baseline * 100`).

> [!IMPORTANT]
> **No Cross-Target Comparisons (Default)**: Performance results from different target platforms (e.g., GKE Node runs vs GCE VM runs) represent distinct environments and MUST NOT be directly compared or labeled as regressions against each other unless explicitly requested by the user.

#### 2. Intra-Run Performance Analysis
Perform intra-run comparisons across protocols and workloads:
- **Sequential vs Random Reads**: Quantify performance characteristics across file size spectrum for both sequential streaming and random access.
- **gRPC vs HTTP/1.1**: Quantify the throughput gain and latency reduction of gRPC relative to HTTP/1.1 across both read and write workloads.
- **NUMA Binding vs Non-NUMA Binding**: Calculate the performance impact of CPU/NUMA node pinning compared to unpinned runs.

#### 3. Strict NPI Performance Pass/Fail Gate (SLA Gate)
Evaluate the run results against the strict NPI performance gate:
- **Full Benchmark Mode**: Sequential reads of **1 GiB file size**, **1M block size**, **128 numjobs**, **10 files** (NR_FILES), without GCSFuse caches. Threshold: maximum throughput MUST be **>= 20 GB/s** for BOTH HTTP/1.1 and gRPC protocols in standard, non-NUMA-pinned configurations.
- **Smoke Test Mode Adaptation**: If running in smoke test mode (or if 1 GiB file size metrics are absent due to scaled parameters), evaluate metrics dynamically from available file sizes (e.g. 1M/100M). Set Executive Summary verdict to:
  `STATUS: SKIPPED (Smoke Test Run - Scaled Parameters: <FILE_SIZE>, <NUMJOBS> jobs)`
  Do NOT mark a smoke test as `FAIL / REJECTED` simply because 1 GiB full workload parameters were not executed.

### Step 3: Verify Machine Type Configuration
Check if the GCE VM or GKE node machine type (e.g., `c4-standard-96` or `ct6e-standard-4t`) is registered in `params.yaml` in the GCSFuse repository:
1. Open `params.yaml`.
2. Search for the machine family/type under high-performance machine listings.
3. If missing, record a required follow-up action to open a PR adding the machine type.

### Step 4: Generate `npi_validation_report.md`
Write the validation report using the standard template, ensuring separate sections for Sequential Reads, Random Reads, and Writes:

```markdown
# GCSFuse NPI Validation Report

## Executive Summary
[Explicit PASS/FAIL verdict for 20 GB/s SLA gate on 1G file size non-NUMA-pinned runs for BOTH HTTP/1.1 and gRPC in full runs.]

## Run Details
- **Timestamp**: [ISO 8601 Timestamp]
- **Target Platforms**: [e.g., GCE VM kislayk-npi2, GKE Cluster gke-orbax-benchmark-cluster]

## Target Performance Results

### [TARGET_NAME_1] (Storage Tier)

#### A. Sequential Read Performance (`read`)
| Block Size | File Size | Throughput (MB/s) | IOPS | Mean Latency (ms) |
|---|---|---|---|---|

#### B. Random Read Performance (`randread`)
| Block Size | File Size | Throughput (MB/s) | IOPS | Mean Latency (ms) |
|---|---|---|---|---|

#### C. Streaming Write Performance (`write`)
| Block Size | File Size | Throughput (MB/s) | IOPS | Mean Latency (ms) |
|---|---|---|---|---|

## High-Performance Machine Type Classification
- **Machine Type Used**: `ct6e-standard-4t`
- **Configured in `params.yaml`?**: [Yes/No]
- **Action Required**: [None / Create PR in GCSFuse repo to add machine type]

## Observations & Issues
- [Detail errors, e.g., TLS Handshake Errors, GKE OOMs, Direct Path fallback issues.]
```

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **Omission of Random Reads** | Table aggregation without `rw` parameter filter | Query `JSON_VALUE(fio_json_output, '$."global options".rw')` and generate separate tables for `read` and `randread`. |
| **Non-Pinned Throughput < 20 GB/s** | CPU/Network saturation or missing host OS offloads in standard run | Mark overall NPI validation as **FAIL / REJECTED** in Executive Summary, even if NUMA-pinned runs pass. Dispatch `remediation-advisor`. |
| **JSON Query Returns `NULL`** | Unquoted spacing in key `"fio version"` in FIO JSON output | Modify SQL query to use `JSON_VALUE(fio_json_output, '$."fio version"')` with escape quotes. |
| **Missing Baseline Dataset** | Baseline BQ table does not exist or dataset path is invalid | Fall back gracefully to intra-run comparisons (gRPC vs HTTP/1.1, NUMA vs non-NUMA). Document absence of baseline in report. |
