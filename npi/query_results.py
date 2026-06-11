#!/usr/bin/env python3
import json
import subprocess
import sys

def get_average_bandwidth(project_id, dataset_id, table_id):
    query = f"""
    SELECT
      AVG(SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64)) / 1024.0 AS avg_bw_mib
    FROM
      `{project_id}.{dataset_id}.{table_id}`,
      UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
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
        if results and results[0].get("avg_bw_mib") is not None:
            return float(results[0]["avg_bw_mib"])
    except Exception as e:
        pass
    return 0.0

def main():
    project_id = "gcs-fuse-test-ml"
    dataset_base_on = "npi_benchmarks_baseline_lro_on"
    dataset_base_off = "npi_benchmarks_baseline_lro_off"
    dataset_reg_on = "npi_benchmarks_regression_lro_on"
    
    protocols = ["http1", "grpc"]
    
    print("\n=======================================================")
    print("===            PERFORMANCE COMPARISON               ===")
    print("=======================================================")
    print(f"{'Protocol':<10} | {'Baseline (1.34.8) LRO ON':<25} | {'Baseline (1.34.8) LRO OFF':<25} | {'Regression (1.35.3) LRO ON':<25}")
    print("-" * 95)
    
    for proto in protocols:
        table_id = f"go_client_read_{proto}"
        
        bw_base_on = get_average_bandwidth(project_id, dataset_base_on, table_id)
        bw_base_off = get_average_bandwidth(project_id, dataset_base_off, table_id)
        bw_reg_on = get_average_bandwidth(project_id, dataset_reg_on, table_id)
        
        bw_base_on_str = f"{bw_base_on:.2f} MiB/s" if bw_base_on > 0 else "N/A"
        bw_base_off_str = f"{bw_base_off:.2f} MiB/s" if bw_base_off > 0 else "N/A"
        bw_reg_on_str = f"{bw_reg_on:.2f} MiB/s" if bw_reg_on > 0 else "FAILED / N/A"
        
        # Special case: we know HTTP1 failed on 1.35.3 LRO ON
        if proto == "http1" and bw_reg_on == 0:
            bw_reg_on_str = "FAILED (TLS Handshake Error)"
            
        print(f"{proto.upper():<10} | {bw_base_on_str:<25} | {bw_base_off_str:<25} | {bw_reg_on_str:<25}")

if __name__ == "__main__":
    main()
