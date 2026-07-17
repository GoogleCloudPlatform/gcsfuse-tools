#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys

def get_table_metrics(project_id, dataset_id, table_id):
    """Queries BigQuery table for average read/write bandwidth and FIO version using escaped JSON key path."""
    if table_id.startswith("go_client_"):
        query = f"""
    SELECT
      'go-client' AS fio_version,
      AVG(SAFE_CAST(read_bw_mbps AS FLOAT64)) AS avg_read_bw_mbs,
      0.0 AS avg_write_bw_mbs
    FROM
      `{project_id}.{dataset_id}.{table_id}`
    """
    else:
        query = f"""
    SELECT
      JSON_VALUE(fio_json_output, '$."fio version"') AS fio_version,
      AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) * 1024.0 / 1000000.0 AS avg_read_bw_mbs,
      AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64)) * 1024.0 / 1000000.0 AS avg_write_bw_mbs
    FROM
      `{project_id}.{dataset_id}.{table_id}`,
      UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
    GROUP BY 1
    """
    cmd = [
        "bq",
        "query",
        f"--project_id={project_id}",
        "--use_legacy_sql=false",
        "--format=json",
        query,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        results = json.loads(res.stdout)
        if results and isinstance(results, list):
            row = results[0]
            read_bw = float(row.get("avg_read_bw_mbs") or 0.0)
            write_bw = float(row.get("avg_write_bw_mbs") or 0.0)
            fio_ver = row.get("fio_version") or "unknown"
            return {"read_bw_mbs": read_bw, "write_bw_mbs": write_bw, "fio_version": fio_ver}
    except Exception:
        pass
    return {"read_bw_mbs": 0.0, "write_bw_mbs": 0.0, "fio_version": "N/A"}

def main():
    parser = argparse.ArgumentParser(description="Query NPI benchmark results from BigQuery.")
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID", "gcs-fuse-test"), help="GCP Project ID")
    parser.add_argument("--dataset-id", required=True, help="Current run BigQuery dataset ID")
    parser.add_argument("--baseline-dataset-id", default=None, help="Optional baseline BigQuery dataset ID for comparison")
    parser.add_argument(
        "--table-types",
        nargs="+",
        default=["fio_read_http1", "fio_read_grpc", "fio_write_http1", "fio_write_grpc", "go_client_read_http1", "go_client_read_grpc"],
        help="Table IDs to query"
    )

    args = parser.parse_args()

    print("\n=========================================================================")
    print("===                  NPI BENCHMARK QUERY RESULTS                      ===")
    print("=========================================================================")
    print(f"Project ID: {args.project_id}")
    print(f"Dataset ID: {args.dataset_id}")
    if args.baseline_dataset_id:
        print(f"Baseline Dataset ID: {args.baseline_dataset_id}")
    print("-" * 80)
    
    header = f"{'Table ID':<25} | {'Read (MB/s)':<15} | {'Write (MB/s)':<15} | {'Baseline Read':<15} | {'Delta (%)':<10}"
    print(header)
    print("-" * 80)

    for table_id in args.table_types:
        metrics = get_table_metrics(args.project_id, args.dataset_id, table_id)
        read_str = f"{metrics['read_bw_mbs']:.2f}" if metrics['read_bw_mbs'] > 0 else "N/A"
        write_str = f"{metrics['write_bw_mbs']:.2f}" if metrics['write_bw_mbs'] > 0 else "N/A"
        
        base_read_str = "N/A"
        delta_str = "N/A"
        
        if args.baseline_dataset_id:
            base_metrics = get_table_metrics(args.project_id, args.baseline_dataset_id, table_id)
            if base_metrics['read_bw_mbs'] > 0:
                base_read_str = f"{base_metrics['read_bw_mbs']:.2f}"
                if metrics['read_bw_mbs'] > 0:
                    delta = ((metrics['read_bw_mbs'] - base_metrics['read_bw_mbs']) / base_metrics['read_bw_mbs']) * 100.0
                    delta_str = f"{delta:+.1f}%"
                    
        print(f"{table_id:<25} | {read_str:<15} | {write_str:<15} | {base_read_str:<15} | {delta_str:<10}")

if __name__ == "__main__":
    main()
