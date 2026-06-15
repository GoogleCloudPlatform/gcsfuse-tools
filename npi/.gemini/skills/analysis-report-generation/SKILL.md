---
name: analysis-report-generation
description: Guides on querying benchmark results from BigQuery, comparing performance metrics (throughput/latency) against baselines, and generating a structured validation report.
---

# GCSFuse NPI Analysis & Report Generation

This skill guides you through querying benchmark results from BigQuery tables, performing analysis on throughput and latency trends against historical baselines, verifying machine type configuration optimizations, and compiling the findings into a standard `npi_validation_report.md`.

## Prerequisites

1.  **GCP/BigQuery Access**: The environment must have access to BigQuery dataset containing the benchmark outputs.
2.  **Baselines Datasets**: Ensure you know the baseline dataset ID (e.g. `npi_benchmarks_baseline_lro_on` or similar) and the newly generated run's dataset ID.
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
  AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) / 1024.0 AS avg_read_bw_mib,
  AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64)) / 1024.0 AS avg_write_bw_mib,
  AVG(SAFE_CAST(JSON_VALUE(job.read.clat_ns.mean) AS FLOAT64)) / 1000000.0 AS avg_read_clat_ms
FROM
  \`<PROJECT_ID>.<DATASET_ID>.<TABLE_ID>\`,
  UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
GROUP BY 1, 2, 3
ORDER BY run_timestamp DESC"
```

### Step 2: Compare Against Baselines

Execute comparison scripts (e.g. `query_results.py`) or calculate the percentage difference in throughput/latency between baseline and regression datasets.

> [!IMPORTANT]
> **No Cross-Target Comparisons**: Performance results from different targets (e.g., GKE Node runs vs GCE VM runs) represent distinct platforms and are not directly comparable. Do not compare them against each other, compute cross-target deltas, or label differences between them as regressions. Compare each environment exclusively against its own respective historical baseline.

Example Comparison Matrix:
| Protocol | Baseline Throughput (MiB/s) | New Run Throughput (MiB/s) | Delta (%) | Status |
| :--- | :--- | :--- | :--- | :--- |
| HTTP/1.1 | 1240.5 | 1235.2 | -0.4% | Neutral |
| gRPC | 3450.0 | 2890.5 | -16.2% | **REGRESSION** |

### Step 3: Verify Machine Type Configuration

Verify if the GCE VM or GKE node machine type (e.g., `c4-standard-96`) is classified under the high-performance machine types in the main GCSFuse repository:
1.  Locate `params.yaml` in the cloned GCSFuse repository.
2.  Search for the machine family or type.
3.  If missing, note it in the validation report as a required follow-up task (PR creation to add family).

### Step 4: Generate `npi_validation_report.md`

Compile the queried results, baselines comparison, and machine family configuration verification into `npi_validation_report.md`.

The report must follow this structure:
```markdown
# GCSFuse NPI Validation Report

## Executive Summary
[Brief description of whether the release/run meets performance criteria and if any regressions/failures were detected.]

## Run Details
- **Timestamp**: [ISO 8601 Timestamp]
- **Target Platform**: [e.g. GCE VM c4-standard-96 / GKE TPU Node]
- **GCSFuse Version**: [e.g. v3.9.0]
- **Target Bucket**: [RAPID / Regional]

## Performance Metrics Comparison
| Benchmark / Protocol | Baseline (Version) | Current Run (Version) | Delta (%) | Status |
|---|---|---|---|---|
| HTTP1 Read | 1250 MiB/s | 1240 MiB/s | -0.8% | PASS |
| gRPC Read | 3500 MiB/s | 2800 MiB/s | -20.0% | FAIL (Regression) |

## High-Performance Machine Type Classification
- **Machine Type Used**: `c4-standard-96`
- **Configured in `params.yaml`?**: [Yes/No]
- **Action Required**: [None / Create PR in GCSFuse repo to add the machine type]

## Observations & Issues
- [Detail any errors observed, e.g., TLS Handshake Errors, GKE OOMs, Direct Path fallback issues.]
```
