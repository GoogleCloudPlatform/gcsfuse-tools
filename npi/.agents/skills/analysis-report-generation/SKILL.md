---
name: analysis-report-generation
description: Guides on querying benchmark results from BigQuery tables via bq query or query_results.py, evaluating throughput and latency metrics against baselines or intra-run configurations, verifying params.yaml machine type classification, assessing the strict 20 GB/s SLA gate on non-pinned runs, and generating the structured npi_validation_report.md deliverable while handling JSON key spacing errors and missing baseline fallbacks.
---

# GCSFuse NPI Analysis & Report Generation

This skill guides you through querying benchmark results from BigQuery tables, performing analysis on throughput and latency trends against historical baselines, verifying machine type configuration optimizations in `params.yaml`, evaluating the strict 20 GB/s SLA performance gate, and compiling the findings into a standardized `npi_validation_report.md`.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **GCP / BigQuery Access**: The environment must have access rights and `bq` CLI credentials to query the BigQuery datasets containing benchmark run outputs.
2. **Baselines Datasets (Optional)**: Access to baseline dataset IDs (e.g., `npi_benchmarks_baseline_lro_on`) if performing historical baseline comparisons. If baselines are unavailable, intra-run comparative analysis is required.
3. **GCSFuse Source Code**: Access to the local GCSFuse repository checkout to inspect `params.yaml` for machine type classification.

### Trigger Conditions
- Benchmark execution (`npi_orchestrator.py`) has completed and metrics are exported to BigQuery.
- Requesting an official validation report (`npi_validation_report.md`) for GCSFuse NPI release or platform qualification.
- Evaluating whether a target machine type and protocol meet the strict 20 GB/s SLA gate requirements.

## Input/Output Contract

### Inputs
- **BigQuery Datasets & Tables**:
  - `<PROJECT_ID>.<DATASET_ID>.host_info` (System specifications and hardware profile)
  - `<PROJECT_ID>.<DATASET_ID>.fio_<benchmark>` (e.g., `fio_read_grpc`, `fio_write_grpc`, `fio_read_parallel`)
  - `<PROJECT_ID>.<DATASET_ID>.go_client_read_<config>` (Go SDK client benchmark metrics)
- **Baseline Dataset ID** (Optional): Historical BigQuery dataset for regression comparison.
- **`params.yaml`**: GCSFuse repository file located at `params.yaml` for machine type verification.
- **Target Metadata**: `targets.json` specifying platform type (GCE VM vs GKE Cluster) and target names.

### Outputs
- **`npi_validation_report.md`**: Main validation report containing:
  - Executive Summary with explicit **PASS / FAIL / REJECTED** verdict for the 20 GB/s SLA gate.
  - System Specifications & Hardware Profiles table populated from `host_info`.
  - Baseline and Intra-Run Performance Comparison tables (HTTP/1.1 vs gRPC, NUMA vs non-NUMA).
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

#### 2. Query Performance Metrics (FIO JSON Handling)
> [!IMPORTANT]
> **JSON Key Spacing**: In FIO JSON output, the version is stored under the key `"fio version"` (with a space). Always query it using the quoted format `JSON_VALUE(fio_json_output, '$."fio version"')` to avoid returning `NULL`.

Execute the performance query via the `bq` CLI tool:
```bash
bq query --project_id=<PROJECT_ID> --use_legacy_sql=false \
"SELECT
  run_timestamp,
  iteration,
  JSON_VALUE(fio_json_output, '\$.\"fio version\"') AS fio_version,
  AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) * 1024.0 / 1000000.0 AS avg_read_bw_mbs,
  AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64)) * 1024.0 / 1000000.0 AS avg_write_bw_mbs,
  AVG(SAFE_CAST(JSON_VALUE(job.read.clat_ns.mean) AS FLOAT64)) / 1000000.0 AS avg_read_clat_ms
FROM
  \`<PROJECT_ID>.<DATASET_ID>.<TABLE_ID>\`,
  UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
WHERE
  block_size = '1m' AND numjobs = 128 AND nr_files = 10 AND file_size = '1G'
GROUP BY 1, 2, 3
ORDER BY run_timestamp DESC"
```

### Step 2: Compare Against Baselines & Perform Intra-Run Analysis

#### 1. Baseline Performance Comparison
If a baseline BigQuery dataset is available, calculate the percentage throughput and latency deltas (`(New - Baseline) / Baseline * 100`).

> [!IMPORTANT]
> **No Cross-Target Comparisons (Default)**: Performance results from different target platforms (e.g., GKE Node runs vs GCE VM runs) represent distinct environments and MUST NOT be directly compared or labeled as regressions against each other unless explicitly requested by the user.

Example Baseline Table:
| Benchmark / Protocol | Baseline Throughput (MB/s) | Current Run Throughput (MB/s) | Delta (%) | Status |
| :--- | :--- | :--- | :--- | :--- |
| HTTP/1.1 Read | 1240.5 | 1235.2 | -0.4% | PASS |
| gRPC Read | 3450.0 | 2890.5 | -16.2% | **FAIL (Regression)** |

#### 2. Intra-Run Performance Analysis
Perform intra-run comparisons across protocols and NUMA configurations:
- **gRPC vs HTTP/1.1**: Quantify the throughput gain and latency reduction of gRPC relative to HTTP/1.1.
- **NUMA Binding vs Non-NUMA Binding**: Calculate the performance impact of CPU/NUMA node pinning compared to unpinned runs.

Example Intra-Run Table:
| Comparison Type | Configuration A | Configuration B | Throughput A (MB/s) | Throughput B (MB/s) | Delta (%) | Status / Insight |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Protocol | HTTP/1.1 | gRPC | 1235.2 | 2890.5 | +134.0% | gRPC shows expected scaling |
| NUMA Binding | Non-NUMA | NUMA-Bound | 2500.0 | 2890.5 | +15.6% | NUMA binding improves throughput |

#### 3. Strict NPI Performance Pass/Fail Gate (SLA Gate)
Evaluate the run results against the strict NPI performance gate:
- **Target Workload**: Sequential reads of **1 GiB file size**, **1M block size**, **128 numjobs**, **10 files** (NR_FILES), without GCSFuse caches.
- **Performance Threshold**: Maximum throughput MUST be **>= 20 GB/s** for BOTH HTTP/1.1 and gRPC protocols.
- **NUMA Pinning Constraint**: The 20 GB/s target **MUST be achieved in standard, non-NUMA-pinned configurations**. If non-pinned runs fail to reach 20 GB/s, the overall verdict MUST be **FAIL / REJECTED**, even if NUMA-pinned runs exceed 20 GB/s.

### Step 3: Verify Machine Type Configuration
Check if the GCE VM or GKE node machine type (e.g., `c4-standard-96`) is registered in `params.yaml` in the GCSFuse repository:
1. Open `params.yaml`.
2. Search for the machine family/type under high-performance machine listings.
3. If missing, record a required follow-up action to open a PR adding the machine type.

### Step 4: Generate `npi_validation_report.md`
Write the validation report using the standard template:

```markdown
# GCSFuse NPI Validation Report

## Executive Summary
[Explicit PASS/FAIL verdict for 20 GB/s SLA gate on 1G file size, 1M block size, 128 numjobs non-NUMA-pinned runs for BOTH HTTP/1.1 and gRPC. If non-pinned throughput < 20 GB/s for either protocol, mark as FAIL / REJECTED.]

## Run Details
- **Timestamp**: [ISO 8601 Timestamp]
- **Target Platforms**: [e.g., GCE VM kislayk-npi2, GKE Cluster gke-orbax-benchmark-cluster]

## System Specifications (Hardware Profile)
| Target Name | Platform Type | OS & Kernel | CPU (Model & Cores) | Total RAM (GB) | Disk Buffer / Cache (Type & Size) | TPU Accelerator |
|---|---|---|---|---|---|---|
| `kislayk-npi2` | GCE VM | Linux 6.1.0 | Intel Xeon (96 cores) | 360 GB | RAID0 SSD (/mnt/lssd, 2.9TB) | N/A |

## Target Performance Results

### [TARGET_NAME_1] (Platform Type)
- **GCSFuse Version**: [e.g. v3.9.0]
- **Target Bucket**: [RAPID / Regional]

#### Baseline Performance Comparison (If Available)
| Benchmark / Protocol | Baseline (Version) | Current Run (Version) | Delta (%) | Status |
|---|---|---|---|---|
| HTTP1 Read | 1250 MB/s | 1240 MB/s | -0.8% | PASS |
| gRPC Read | 3500 MB/s | 2800 MB/s | -20.0% | FAIL (Regression) |

#### Intra-Run Performance Analysis
##### gRPC vs HTTP/1.1 Protocol Comparison
| Metric | HTTP/1.1 | gRPC | Delta (%) | Observation |
|---|---|---|---|---|
| Read Throughput | 1240 MB/s | 2800 MB/s | +125.8% | gRPC outperforms HTTP/1.1 |

##### NUMA Binding vs Non-NUMA Binding Analysis
| Protocol / Workload | Non-NUMA Bound | NUMA Bound | Delta (%) | Observation |
|---|---|---|---|---|
| gRPC Read Throughput | 2400 MB/s | 2800 MB/s | +16.7% | NUMA binding improves throughput |

## High-Performance Machine Type Classification
- **Machine Type Used**: `c4-standard-96`
- **Configured in `params.yaml`?**: [Yes/No]
- **Action Required**: [None / Create PR in GCSFuse repo to add machine type]

## Observations & Issues
- [Detail errors, e.g., TLS Handshake Errors, GKE OOMs, Direct Path fallback issues.]
```

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **Non-Pinned Throughput < 20 GB/s** | CPU/Network saturation or missing host OS offloads in standard run | Mark overall NPI validation as **FAIL / REJECTED** in Executive Summary, even if NUMA-pinned runs pass. Dispatch `remediation-advisor`. |
| **JSON Query Returns `NULL`** | Unquoted spacing in key `"fio version"` in FIO JSON output | Modify SQL query to use `JSON_VALUE(fio_json_output, '$."fio version"')` with escape quotes. |
| **Missing Baseline Dataset** | Baseline BQ table does not exist or dataset path is invalid | Fall back gracefully to intra-run comparisons (gRPC vs HTTP/1.1, NUMA vs non-NUMA). Document absence of baseline in report. |
| **Invalid Cross-Target Comparison** | Attempting to compare GCE VM vs GKE Cluster directly | Do NOT perform cross-target comparison or flag deltas as regressions unless explicitly requested by user. Keep target performance tables isolated. |
| **Machine Family Missing in `params.yaml`** | New GCE machine type (e.g., `c4`) not registered in GCSFuse repo | Document item under High-Performance Machine Type Classification section of report as a required PR task. |

## Verification Checks

1. **File Existence Check**: Verify that `npi_validation_report.md` exists and is non-empty:
   ```bash
   test -s npi_validation_report.md && echo "Report generated successfully"
   ```
2. **SLA Verdict Verification**: Confirm that Executive Summary explicitly contains a PASS or FAIL / REJECTED statement regarding the 20 GB/s non-pinned SLA gate:
   ```bash
   grep -E "(PASS|FAIL|REJECTED)" npi_validation_report.md
   ```
3. **Hardware Specs Verification**: Ensure system specification table fields (CPU, RAM, OS, Disk Buffer) are fully populated from BigQuery `host_info` without missing `N/A` placeholders for valid metrics.
