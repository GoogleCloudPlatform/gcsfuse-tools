#!/usr/bin/env python3
"""A script for running GCSfuse performance benchmarks on a GKE cluster.

This script orchestrates running read/write x HTTP1/gRPC benchmarks in Kubernetes
Jobs on an existing GKE cluster. It runs them sequentially, clearing the target
BigQuery table before each benchmark, and waits for completion.

Usage:
  python3 npi_gke.py --bucket-name <bucket> --project-id <project> \\
    --bq-dataset-id <dataset> --gcsfuse-version <version>
"""

import argparse
import subprocess
import sys
import time
import yaml
import tempfile
import os

def truncate_bq_table(project_id, dataset_id, table_id):
    """Erases existing data in the specified BigQuery table."""
    if not (project_id and dataset_id and table_id):
        return
    print(f"--- Erasing data in BigQuery table: {project_id}.{dataset_id}.{table_id} ---")
    bq_cmd = [
        "bq", "query", "--use_legacy_sql=false",
        f"TRUNCATE TABLE `{project_id}.{dataset_id}.{table_id}`"
    ]
    try:
        res = subprocess.run(bq_cmd, capture_output=True, text=True)
        if res.returncode != 0 and "Not found:" not in res.stderr:
            print(f"Warning: Failed to truncate BQ table. {res.stderr}")
    except Exception as e:
        print(f"Warning: Failed to execute bq command: {e}")

def create_job_spec(job_name, image, args):
    """Creates a Kubernetes Job spec dictionary from the template yaml."""
    script_dir = os.path.dirname(os.path.realpath(__file__))
    template_path = os.path.join(script_dir, "npi_job_spec.yaml")
    
    with open(template_path, 'r') as f:
        job_spec = yaml.safe_load(f)
        
    job_spec["metadata"]["name"] = job_name
    
    container = job_spec["spec"]["template"]["spec"]["containers"][0]
    container["image"] = image
    container["args"] = args
    
    return job_spec

def run_benchmark_job(job_name, image, args_list, project_id, dataset_id, table_id):
    """Runs a benchmark job on GKE and waits for its completion."""
    # 1. Truncate BQ Table
    truncate_bq_table(project_id, dataset_id, table_id)

    # 2. Cleanup any existing job with the same name
    subprocess.run(["kubectl", "delete", "job", job_name, "--ignore-not-found=true"], capture_output=True)

    # 3. Create Job Spec and Apply
    job_spec = create_job_spec(job_name, image, args_list)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(job_spec, f)
        temp_yaml = f.name

    print(f"--- Submitting Kubernetes Job: {job_name} ---")
    res = subprocess.run(["kubectl", "apply", "-f", temp_yaml], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Failed to apply job {job_name}:\n{res.stderr}", file=sys.stderr)
        os.remove(temp_yaml)
        return False

    os.remove(temp_yaml)

    # 4. Wait for Job to Complete
    print(f"Waiting for Job {job_name} to complete...")
    # Wait until it's either complete or failed
    try:
        subprocess.run(
            ["kubectl", "wait", f"job/{job_name}", "--for=condition=complete", "--timeout=1h"],
            check=True, text=True, capture_output=True
        )
        print(f"--- Job {job_name} finished successfully ---")
        return True
    except subprocess.CalledProcessError as e:
        # It could have failed or timed out
        print(f"--- Job {job_name} FAILED or TIMED OUT ---", file=sys.stderr)
        # Try to get the logs
        print("Fetching logs...", file=sys.stderr)
        pod_res = subprocess.run(
            ["kubectl", "get", "pods", "-l", f"job-name={job_name}", "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True
        )
        if pod_res.returncode == 0 and pod_res.stdout.strip():
            pod_name = pod_res.stdout.strip()
            subprocess.run(["kubectl", "logs", pod_name])
        return False

def main():
    parser = argparse.ArgumentParser(description="GKE benchmark runner for GCSFuse NPI.")
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket to use.")
    parser.add_argument("--project-id", required=True, help="Project ID for results.")
    parser.add_argument("--bq-dataset-id", required=True, help="BigQuery dataset ID for results.")
    parser.add_argument("--gcsfuse-version", required=True, help="GCSFuse version to use for benchmark images (e.g., 'master', 'v1.2.0').")
    parser.add_argument("--iterations", type=int, default=5, help="Number of FIO test iterations per benchmark. Default: 5.")
    
    args = parser.parse_args()

    benchmarks = [
        ("read", "http1", "fio-read-benchmark", ""),
        ("read", "grpc", "fio-read-benchmark", "--client-protocol=grpc"),
        ("write", "http1", "fio-write-benchmark", ""),
        ("write", "grpc", "fio-write-benchmark", "--client-protocol=grpc"),
    ]

    failed_benchmarks = []

    for bench_type, config_name, image_suffix, extra_flag in benchmarks:
        full_bench_name = f"{bench_type}_{config_name}"
        job_name = f"gcsfuse-npi-{full_bench_name}".replace("_", "-")
        bq_table_id = f"fio_{full_bench_name}"
        
        image = f"us-docker.pkg.dev/{args.project_id}/gcsfuse-benchmarks/{image_suffix}-{args.gcsfuse_version}:latest"
        
        gcsfuse_flags = "--temp-dir=/gcsfuse-temp -o allow_other"
        if extra_flag:
            gcsfuse_flags += f" {extra_flag}"

        cmd_args = [
            f"--iterations={args.iterations}",
            f"--project-id={args.project_id}",
            f"--bq-dataset-id={args.bq_dataset_id}",
            f"--bq-table-id={bq_table_id}",
            f"--bucket-name={args.bucket_name}",
            f"--gcsfuse-flags={gcsfuse_flags}"
        ]

        success = run_benchmark_job(
            job_name=job_name,
            image=image,
            args_list=cmd_args,
            project_id=args.project_id,
            dataset_id=args.bq_dataset_id,
            table_id=bq_table_id
        )

        if not success:
            failed_benchmarks.append(full_bench_name)
            print(f"Skipping further benchmarks because {full_bench_name} failed.")
            break

        # Delete job after successful completion to clean up the cluster
        subprocess.run(["kubectl", "delete", "job", job_name, "--ignore-not-found=true"], capture_output=True)

    if failed_benchmarks:
        print(f"\n--- Some benchmarks failed: {', '.join(failed_benchmarks)} ---", file=sys.stderr)
        sys.exit(1)
    else:
        print("\n--- All benchmarks completed successfully! ---")

if __name__ == "__main__":
    main()
