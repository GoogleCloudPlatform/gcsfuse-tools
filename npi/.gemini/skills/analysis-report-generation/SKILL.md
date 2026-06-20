---
name: analysis-report-generation
description: Guides on querying benchmark results from BigQuery, comparing performance metrics (throughput/latency) against baselines, and generating a structured validation report.
---

# GCSFuse NPI Analysis & Report Generation

This skill guides you through querying benchmark results from BigQuery tables, performing analysis on throughput and latency trends against historical baselines, verifying machine type configuration optimizations, and compiling the findings into a standard `npi_validation_report.md`.

## Prerequisites

1.  **GCP/BigQuery Access**: The environment must have access to BigQuery dataset containing the benchmark outputs.
2.  **Baselines Datasets (Optional)**: Ensure you know the baseline dataset ID (e.g. `npi_benchmarks_baseline_lro_on` or similar) and the newly generated run's dataset ID, if available. If no baseline dataset is present, the report must still be generated using intra-run comparisons.
3.  **GCSFuse Source Code**: Access to GCSFuse code is required to inspect `params.yaml` for machine type verification.

## Step-by-Step Procedure

### Step 1: Query BigQuery Results

Retrieve performance data from the respective benchmark tables (e.g., `go_client_read_http1`, `go_client_read_grpc`, `fio_write_grpc`).

> [!IMPORTANT]
> **JSON Key Spacing**: In the FIO JSON output, the version is stored under the key `"fio version"` (with a space). Always query it using the quoted format: `JSON_VALUE(fio_json_output, '$."fio version"')` to avoid returning `NULL`.

Run queries using the `bq` CLI or a python BigQuery client:
```bash
bq query --project_id=<PROJECT_ID> --use_legacy_sql=false \
"SELECT
  run_timestamp,
  iteration,
  JSON_VALUE(fio_json_output, '\$.\"fio version\"') AS fio_version,
  AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) * 1024.0 / 1000000.0 AS avg_read_bw_mb,
  AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64)) * 1024.0 / 1000000.0 AS avg_write_bw_mb,
  AVG(SAFE_CAST(JSON_VALUE(job.read.clat_ns.mean) AS FLOAT64)) / 1000000.0 AS avg_read_clat_ms
FROM
  \`<PROJECT_ID>.<DATASET_ID>.<TABLE_ID>\`,
  UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
GROUP BY 1, 2, 3
ORDER BY run_timestamp DESC"
```

### Step 2: Compare Against Baselines & Perform Intra-Run Analysis

#### 1. Compare Against Baselines (If Baseline Dataset is Available)
If a baseline dataset is available, execute comparison scripts (e.g. `query_results.py`) or calculate the percentage difference in throughput/latency between baseline and regression datasets.

> [!IMPORTANT]
> **No Cross-Target Comparisons (Default)**: Performance results from different targets (e.g., GKE Node runs vs GCE VM runs) represent distinct platforms and are not directly comparable by default. Do not compare them against each other, compute cross-target deltas, or label differences between them as regressions, **unless the user explicitly requests a cross-target platform comparison**. If explicitly requested, you may compare the environments and include a dedicated section in the final report.

Example Comparison Matrix:
| Protocol | Baseline Throughput (MiB/s) | New Run Throughput (MiB/s) | Delta (%) | Status |
| :--- | :--- | :--- | :--- | :--- |
| HTTP/1.1 | 1240.5 | 1235.2 | -0.4% | Neutral |
| gRPC | 3450.0 | 2890.5 | -16.2% | **REGRESSION** |

#### 2. Perform Intra-Run Comparisons (Always Recommended)
Even when a baseline dataset is present, or if it is not present, you should perform intra-run comparisons to analyze and highlight the relative performance gains under different configurations:

*   **gRPC vs HTTP/1**:
    - Compare the performance of gRPC against HTTP/1.1 under the same test workload in the run.
    - Quantify the throughput gain (or loss) and latency delta when using gRPC compared to HTTP/1.1.
*   **NUMA binding vs non-NUMA binding analysis**:
    - Compare performance metrics (throughput and latency) between runs executed with NUMA binding enabled versus runs executed without NUMA binding.
    - Highlight the percentage improvement or degradation introduced by NUMA binding.

Example Intra-Run Comparison Matrix:
| Comparison Type | Configuration A | Configuration B | Throughput A (MiB/s) | Throughput B (MiB/s) | Delta (%) | Status / Insight |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Protocol | HTTP/1.1 | gRPC | 1235.2 | 2890.5 | +134.0% | gRPC shows expected scaling |
| NUMA Binding | Non-NUMA | NUMA-Bound | 2500.0 | 2890.5 | +15.6% | NUMA binding improves throughput |

### Step 3: Verify Machine Type Configuration

Verify if the GCE VM or GKE node machine type (e.g., `c4-standard-96`) is classified under the high-performance machine types in the main GCSFuse repository:
1.  Locate `params.yaml` in the cloned GCSFuse repository.
2.  Search for the machine family or type.
3.  If missing, note it in the validation report as a required follow-up task (PR creation to add family).

### Step 4: Generate `npi_validation_report.md`

Compile the queried results, baselines comparison, and machine family configuration verification into `npi_validation_report.md`.

For each target validation environment executed (GCE VM or GKE Cluster), create a separate section and performance table to isolate their metrics and prevent incorrect direct comparisons.

The report must follow this structure:
```markdown
# GCSFuse NPI Validation Report

## Executive Summary
[Brief description of whether the run meets performance criteria and if any regressions/failures were detected.]

## Run Details
- **Timestamp**: [ISO 8601 Timestamp]
- **Target Platforms**: [List of all target names, e.g. GCE VM target-1, GKE Cluster target-2, etc.]

## Target Performance Results

### [TARGET_NAME_1] (Platform Type, e.g., GCE VM)
- **GCSFuse Version**: [e.g. v3.9.0]
- **Target Bucket**: [RAPID / Regional]
- **Performance Metrics Comparison**:

#### Baseline Performance Comparison (If Baseline is Available)
| Benchmark / Protocol | Baseline (Version) | Current Run (Version) | Delta (%) | Status |
|---|---|---|---|---|
| HTTP1 Read | 1250 MB/s | 1240 MB/s | -0.8% | PASS |
| gRPC Read | 3500 MB/s | 2800 MB/s | -20.0% | FAIL (Regression) |

#### Intra-Run Performance Analysis (If Applicable)
Provide these comparisons if the corresponding protocols or NUMA configurations were executed in the run:

##### gRPC vs HTTP/1.1 Protocol Comparison
| Metric | HTTP/1.1 | gRPC | Delta (%) | Observation |
|---|---|---|---|---|
| Read Throughput | 1240 MB/s | 2800 MB/s | +125.8% | gRPC significantly outperforms HTTP/1.1 |
| Read Latency (mean) | 0.012 ms | 0.005 ms | -58.3% | gRPC shows lower latency |

##### NUMA Binding vs Non-NUMA Binding Analysis
| Protocol / Workload | Non-NUMA Bound | NUMA Bound | Delta (%) | Observation |
|---|---|---|---|---|
| gRPC Read Throughput | 2400 MB/s | 2800 MB/s | +16.7% | NUMA binding improves throughput |
| gRPC Read Latency | 0.006 ms | 0.005 ms | -16.7% | NUMA binding reduces latency |

### Cross-Target Platform Comparison (Only If Explicitly Requested by User)
If the user explicitly requested a comparison between different targets (e.g., GCE VM vs GKE Cluster), compile their metrics into a side-by-side comparison table here:

| Metric / Workload | [TARGET_NAME_1] (e.g., GCE VM) | [TARGET_NAME_2] (e.g., GKE Cluster) | Delta (%) | Status / Observation |
|---|---|---|---|---|
| gRPC Read Throughput | 2800 MB/s | 3100 MB/s | +10.7% | GKE cluster shows higher peak throughput |
| gRPC Read Latency | 0.005 ms | 0.004 ms | -20.0% | GKE cluster shows lower latency |

## High-Performance Machine Type Classification
- **Machine Type Used**: `c4-standard-96`
- **Configured in `params.yaml`?**: [Yes/No]
- **Action Required**: [None / Create PR in GCSFuse repo to add the machine type]

## Observations & Issues
- [Detail any errors observed, e.g., TLS Handshake Errors, GKE OOMs, Direct Path fallback issues.]
```
