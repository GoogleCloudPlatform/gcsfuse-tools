#!/usr/bin/env python3
import os
import sys
import csv
import argparse
import math
from datetime import datetime, timedelta

curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
    sys.path.insert(0, curr_dir)

from perf_gating import (
    bq_query,
    load_workloads,
    get_workload_key,
    get_sql_filter,
    DEFAULT_WORKLOADS_CSV,
)


def _source_query(table, timestamp, commit_col, commit, extra_where=""):
    """Helper to generate a subquery for a specific BigQuery table."""
    commit_clause = f"AND {commit_col} = '{commit}'" if commit else ""
    return f"""
  SELECT io_type, file_size, block_size, num_jobs, config, direct,
         (IFNULL(CAST(write_bw_mbs AS FLOAT64), 0) + IFNULL(CAST(read_bw_mbs AS FLOAT64), 0)) as bw
  FROM `{table}`
  WHERE run_timestamp >= '{timestamp}' {commit_clause} {extra_where}"""


def build_stats_query(workloads, days=30, commit=None, release_version=None, source="periodic", project_id="gcs-fuse-test-ml"):
    """Builds BQ SQL to compute min, max, mean, variance, and stddev for target workloads."""
    ts = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    queries = []
    if source in ("periodic", "all"):
        queries.append(_source_query(f"{project_id}.periodic_benchmarks.kokoro_run_*", ts, "commit", commit))
    if source in ("release", "all"):
        rel_clause = f"AND release_version = '{release_version}'" if release_version else ""
        queries.append(_source_query(f"{project_id}.gcsfuse_release_performance_metrics.v*", ts, "commit_hash", commit, rel_clause))

    if not queries:
        raise ValueError(f"Invalid source: {source}. Choose 'periodic', 'release', or 'all'.")

    union_sql = "\n  UNION ALL\n".join(queries)
    return f"""
WITH raw_data AS ({union_sql}
)
SELECT io_type, file_size, block_size, num_jobs, config, direct,
  COUNT(*) as sample_count, MIN(bw) as min_bw, MAX(bw) as max_bw, AVG(bw) as mean_bw,
  IFNULL(VAR_SAMP(bw), 0.0) as variance_bw, IFNULL(STDDEV_SAMP(bw), 0.0) as stddev_bw
FROM raw_data
WHERE bw > 0 AND {get_sql_filter(workloads)}
GROUP BY io_type, file_size, block_size, num_jobs, config, direct
"""


def calculate_derived_stats(row, min_floor=5.0):
    """Computes variability metrics (CV %, min/max drop %, suggested threshold) from an aggregate BQ row."""
    mean = float(row.get('mean_bw') or 0.0)
    min_bw, max_bw = float(row.get('min_bw') or 0.0), float(row.get('max_bw') or 0.0)
    stddev, variance = float(row.get('stddev_bw') or 0.0), float(row.get('variance_bw') or 0.0)
    count = int(row.get('sample_count') or 0)

    cv_pct = (stddev / mean * 100.0) if mean else 0.0
    min_diff_pct = ((min_bw - mean) / mean * 100.0) if mean else 0.0
    max_diff_pct = ((max_bw - mean) / mean * 100.0) if mean else 0.0
    suggested = max(abs(min_diff_pct), 2.0 * cv_pct, min_floor)

    return {
        "sample_count": count, "mean_bw": mean, "min_bw": min_bw, "max_bw": max_bw,
        "stddev_bw": stddev, "variance_bw": variance, "cv_pct": cv_pct,
        "min_diff_pct": min_diff_pct, "max_diff_pct": max_diff_pct,
        "suggested_threshold_pct": suggested,
    }


def export_to_csv(filepath, rows):
    """Exports structured dictionaries to a CSV file."""
    if not filepath or not rows:
        return
    try:
        with open(filepath, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"Exported statistical analysis summary to {filepath}")
    except Exception as e:
        print(f"Error exporting CSV to {filepath}: {e}")


def analyze_and_print_thresholds(data, workloads=None, min_floor=5.0, output_csv=None):
    """Evaluates target workloads, prints summary statistics table, and returns overall recommended threshold."""
    data_by_key = {get_workload_key(row): row for row in data}
    target_keys = [get_workload_key(w) for w in workloads] if workloads else list(data_by_key.keys())

    header = (
        f"{'Workload':<42} | {'Count':<6} | {'Mean (MB/s)':<11} | {'Min (MB/s)':<10} | "
        f"{'Max (MB/s)':<10} | {'StdDev':<8} | {'Variance':<10} | {'CV %':<7} | "
        f"{'Max Drop %':<10} | {'Suggested %':<11}"
    )
    print("=" * len(header) + f"\n{header}\n" + "-" * len(header))

    max_suggested, csv_rows = min_floor, []
    for w_key in target_keys:
        row = data_by_key.get(w_key)
        if not row:
            print(f"{w_key:<42} | {'N/A':<6} | {'N/A':<11} | {'N/A':<10} | {'N/A':<10} | {'N/A':<8} | {'N/A':<10} | {'N/A':<7} | {'N/A':<10} | {'N/A':<11}")
            continue

        s = calculate_derived_stats(row, min_floor=min_floor)
        max_suggested = max(max_suggested, s["suggested_threshold_pct"])

        print(
            f"{w_key:<42} | {s['sample_count']:<6} | {s['mean_bw']:<11.2f} | {s['min_bw']:<10.2f} | "
            f"{s['max_bw']:<10.2f} | {s['stddev_bw']:<8.2f} | {s['variance_bw']:<10.1f} | "
            f"{s['cv_pct']:<6.2f}% | {s['min_diff_pct']:<+9.2f}% | {s['suggested_threshold_pct']:<10.2f}%"
        )
        csv_rows.append({"workload": w_key, **{k: round(v, 2) if isinstance(v, float) else v for k, v in s.items()}})

    print("-" * len(header))
    overall = math.ceil(max_suggested * 10) / 10.0
    print(f"Overall Recommended Uniform Gating Threshold: +/- {overall:.1f}%\n" + "=" * len(header))
    export_to_csv(output_csv, csv_rows)
    return overall


def main():
    parser = argparse.ArgumentParser(description="Analyze GCSFuse performance metrics to determine gating thresholds.")
    parser.add_argument("--days", type=int, default=30, help="Number of previous days to analyze (default: 30)")
    parser.add_argument("--commit", required=False, help="Filter by specific commit hash")
    parser.add_argument("--release-version", required=False, help="Filter by specific release version (e.g., 3.9.0)")
    parser.add_argument("--source", choices=["periodic", "release", "all"], default="periodic", help="BQ table source (default: periodic)")
    parser.add_argument("--workloads-csv", required=False, default=None, help="Path to CSV file defining target workloads")
    parser.add_argument("--min-floor", type=float, default=5.0, help="Minimum floor for suggested threshold percentage (default: 5.0)")
    parser.add_argument("--project", default="gcs-fuse-test-ml", help="GCP Project ID for BigQuery")
    parser.add_argument("--output-csv", required=False, help="Optional file path to export analysis results as CSV")
    args = parser.parse_args()

    csv_path = args.workloads_csv or DEFAULT_WORKLOADS_CSV
    print(f"Loading target workloads from {csv_path}...")
    workloads = load_workloads(csv_path)

    query = build_stats_query(
        workloads, days=args.days, commit=args.commit,
        release_version=args.release_version, source=args.source, project_id=args.project,
    )
    print("Executing BigQuery statistical aggregation...")
    data = bq_query(query, project_id=args.project)
    analyze_and_print_thresholds(data, workloads, min_floor=args.min_floor, output_csv=args.output_csv)


if __name__ == "__main__":
    main()
