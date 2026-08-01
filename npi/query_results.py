#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys

def get_table_metrics(project_id, dataset_id, table_id):
    """Queries BigQuery table for sequential read, random read, and write bandwidth, latency, and IOPS."""
    if table_id.startswith("go_client_"):
        query = f"""
    SELECT
      'go-client' AS fio_version,
      AVG(SAFE_CAST(read_bw_mbps AS FLOAT64)) AS seq_read_bw_mbs,
      0.0 AS rand_read_bw_mbs,
      0.0 AS write_bw_mbs,
      0.0 AS seq_read_lat_ms,
      0.0 AS rand_read_lat_ms,
      0.0 AS write_lat_ms
    FROM
      `{project_id}.{dataset_id}.{table_id}`
    """
    else:
        query = f"""
    SELECT
      JSON_VALUE(fio_json_output, '$."fio version"') AS fio_version,
      AVG(IF(COALESCE(JSON_VALUE(fio_json_output, '$."global options".rw'), 'read') = 'read', SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64) * 1024.0 / 1000000.0, NULL)) AS seq_read_bw_mbs,
      AVG(IF(JSON_VALUE(fio_json_output, '$."global options".rw') = 'randread', SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64) * 1024.0 / 1000000.0, NULL)) AS rand_read_bw_mbs,
      AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64) * 1024.0 / 1000000.0) AS write_bw_mbs,
      AVG(IF(COALESCE(JSON_VALUE(fio_json_output, '$."global options".rw'), 'read') = 'read', SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64) / 1000000.0, NULL)) AS seq_read_lat_ms,
      AVG(IF(JSON_VALUE(fio_json_output, '$."global options".rw') = 'randread', SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64) / 1000000.0, NULL)) AS rand_read_lat_ms,
      AVG(SAFE_CAST(JSON_VALUE(job.write.lat_ns.mean) AS FLOAT64) / 1000000.0) AS write_lat_ms
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
            seq_read_bw = float(row.get("seq_read_bw_mbs") or 0.0)
            rand_read_bw = float(row.get("rand_read_bw_mbs") or 0.0)
            write_bw = float(row.get("write_bw_mbs") or 0.0)
            seq_read_lat = float(row.get("seq_read_lat_ms") or 0.0)
            rand_read_lat = float(row.get("rand_read_lat_ms") or 0.0)
            write_lat = float(row.get("write_lat_ms") or 0.0)
            fio_ver = row.get("fio_version") or "unknown"
            return {
                "seq_read_bw_mbs": seq_read_bw,
                "rand_read_bw_mbs": rand_read_bw,
                "write_bw_mbs": write_bw,
                "seq_read_lat_ms": seq_read_lat,
                "rand_read_lat_ms": rand_read_lat,
                "write_lat_ms": write_lat,
                "fio_version": fio_ver
            }
    except Exception:
        pass
    return {
        "seq_read_bw_mbs": 0.0,
        "rand_read_bw_mbs": 0.0,
        "write_bw_mbs": 0.0,
        "seq_read_lat_ms": 0.0,
        "rand_read_lat_ms": 0.0,
        "write_lat_ms": 0.0,
        "fio_version": "N/A"
    }

def get_detailed_table_metrics(project_id, dataset_id, table_id):
    """Queries BigQuery table for granular breakdown by file_size and block_size."""
    if table_id.startswith("go_client_"):
        return []
    query = f"""
    SELECT
      JSON_VALUE(job, '$.\"job options\".bs') AS block_size,
      JSON_VALUE(job, '$.\"job options\".filesize') AS file_size,
      COALESCE(JSON_VALUE(fio_json_output, '$.\"global options\".rw'), 'write') AS workload_type,
      ROUND(AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) * 1024.0 / 1000000.0, 2) AS read_bw_mbs,
      ROUND(AVG(SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64)) / 1000000.0, 2) AS read_lat_ms,
      ROUND(AVG(SAFE_CAST(JSON_VALUE(job.read.iops) AS FLOAT64)), 2) AS read_iops,
      ROUND(AVG(SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64)) * 1024.0 / 1000000.0, 2) AS write_bw_mbs,
      ROUND(AVG(SAFE_CAST(JSON_VALUE(job.write.lat_ns.mean) AS FLOAT64)) / 1000000.0, 2) AS write_lat_ms,
      ROUND(AVG(SAFE_CAST(JSON_VALUE(job.write.iops) AS FLOAT64)), 2) AS write_iops
    FROM
      `{project_id}.{dataset_id}.{table_id}`,
      UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
    GROUP BY 1, 2, 3
    ORDER BY 
      CASE workload_type WHEN 'read' THEN 1 WHEN 'randread' THEN 2 ELSE 3 END,
      CASE block_size WHEN '16K' THEN 1 WHEN '128K' THEN 2 WHEN '1M' THEN 3 ELSE 4 END,
      CASE file_size 
        WHEN '128K' THEN 1 
        WHEN '256K' THEN 2 
        WHEN '1M' THEN 3 
        WHEN '5M' THEN 4 
        WHEN '10M' THEN 5 
        WHEN '50M' THEN 6 
        WHEN '100M' THEN 7 
        WHEN '200M' THEN 8 
        WHEN '1G' THEN 9 
        ELSE 10 
      END
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
            return results
    except Exception:
        pass
    return []

def main():
    parser = argparse.ArgumentParser(description="Query NPI benchmark results from BigQuery with explicit read type disaggregation.")
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID", "gcs-fuse-test"), help="GCP Project ID")
    parser.add_argument("--dataset-id", required=True, help="Current run BigQuery dataset ID")
    parser.add_argument("--baseline-dataset-id", default=None, help="Optional baseline BigQuery dataset ID for comparison")
    parser.add_argument(
        "--table-types",
        nargs="+",
        default=["fio_read_http1", "fio_read_grpc", "fio_write_http1", "fio_write_grpc", "go_client_read_http1", "go_client_read_grpc"],
        help="Table IDs to query"
    )
    parser.add_argument("--detailed", action="store_true", help="Print granular metrics broken down by file size and block size")

    args = parser.parse_args()

    print("\n=========================================================================================================")
    print("===                                 NPI BENCHMARK QUERY RESULTS                                       ===")
    print("=========================================================================================================")
    print(f"Project ID: {args.project_id}")
    print(f"Dataset ID: {args.dataset_id}")
    if args.baseline_dataset_id:
        print(f"Baseline Dataset ID: {args.baseline_dataset_id}")
    print("-" * 105)
    
    header = f"{'Table ID':<22} | {'Seq Read (MB/s)':<16} | {'Rand Read (MB/s)':<16} | {'Write (MB/s)':<14} | {'Base Seq':<10} | {'Base Rand':<10} | {'Delta Seq':<10}"
    print(header)
    print("-" * 105)

    for table_id in args.table_types:
        metrics = get_table_metrics(args.project_id, args.dataset_id, table_id)
        seq_read_str = f"{metrics['seq_read_bw_mbs']:.2f}" if metrics['seq_read_bw_mbs'] > 0 else "N/A"
        rand_read_str = f"{metrics['rand_read_bw_mbs']:.2f}" if metrics['rand_read_bw_mbs'] > 0 else "N/A"
        write_str = f"{metrics['write_bw_mbs']:.2f}" if metrics['write_bw_mbs'] > 0 else "N/A"
        
        base_seq_str = "N/A"
        base_rand_str = "N/A"
        delta_seq_str = "N/A"
        
        if args.baseline_dataset_id:
            base_metrics = get_table_metrics(args.project_id, args.baseline_dataset_id, table_id)
            if base_metrics['seq_read_bw_mbs'] > 0:
                base_seq_str = f"{base_metrics['seq_read_bw_mbs']:.2f}"
                if metrics['seq_read_bw_mbs'] > 0:
                    delta_seq = ((metrics['seq_read_bw_mbs'] - base_metrics['seq_read_bw_mbs']) / base_metrics['seq_read_bw_mbs']) * 100.0
                    delta_seq_str = f"{delta_seq:+.1f}%"
            if base_metrics['rand_read_bw_mbs'] > 0:
                base_rand_str = f"{base_metrics['rand_read_bw_mbs']:.2f}"
                    
        print(f"{table_id:<22} | {seq_read_str:<16} | {rand_read_str:<16} | {write_str:<14} | {base_seq_str:<10} | {base_rand_str:<10} | {delta_seq_str:<10}")

    if args.detailed:
        print("\n" + "=" * 105)
        print("===                               DETAILED METRICS BREAKDOWN                                          ===")
        print("=" * 105)
        for table_id in args.table_types:
            rows = get_detailed_table_metrics(args.project_id, args.dataset_id, table_id)
            if not rows:
                continue
            print(f"\n--- Detailed Table: {table_id} ---")
            det_header = f"{'Type':<12} | {'Block Size':<12} | {'File Size':<12} | {'Read (MB/s)':<14} | {'Read Lat (ms)':<14} | {'Write (MB/s)':<14} | {'Write Lat (ms)':<14}"
            print(det_header)
            print("-" * 105)
            for r in rows:
                w_type = r.get("workload_type") or "N/A"
                bs = r.get("block_size") or "N/A"
                fs = r.get("file_size") or "N/A"
                rbw = f"{float(r.get('read_bw_mbs') or 0):.2f}" if float(r.get('read_bw_mbs') or 0) > 0 else "N/A"
                rlat = f"{float(r.get('read_lat_ms') or 0):.2f}" if float(r.get('read_lat_ms') or 0) > 0 else "N/A"
                wbw = f"{float(r.get('write_bw_mbs') or 0):.2f}" if float(r.get('write_bw_mbs') or 0) > 0 else "N/A"
                wlat = f"{float(r.get('write_lat_ms') or 0):.2f}" if float(r.get('write_lat_ms') or 0) > 0 else "N/A"
                print(f"{w_type:<12} | {bs:<12} | {fs:<12} | {rbw:<14} | {rlat:<14} | {wbw:<14} | {wlat:<14}")

if __name__ == "__main__":
    main()
