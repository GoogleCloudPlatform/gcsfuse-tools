#!/usr/bin/env python3
"""A script for running GCSfuse performance benchmarks on a GKE cluster.

This script orchestrates running read/write x HTTP1/gRPC benchmarks in Kubernetes
Jobs on an existing GKE cluster. It runs them sequentially, clearing the target
BigQuery table before each benchmark, and waits for completion.

Usage:
  python3 npi_gke.py --bucket-name <bucket> --project-id <project> \\
    --bq-dataset-id <dataset> 
"""

import argparse
import subprocess
import sys
import time
import yaml
import os
import datetime

def create_job_spec(job_name, image, args, bucket_name, service_account, extra_flag=None):
    """Creates a Kubernetes Job spec dictionary from the template yaml."""
    script_dir = os.path.dirname(os.path.realpath(__file__))
    template_path = os.path.join(script_dir, "npi_job_spec.yaml")
    
    with open(template_path, 'r') as f:
        job_spec = yaml.safe_load(f)
        
    job_spec["metadata"]["name"] = job_name
    
    pod_spec = job_spec["spec"]["template"]["spec"]
    
    # Replace service account
    pod_spec["serviceAccountName"] = service_account
    
    # Replace bucket name in CSI volume
    for vol in pod_spec.get("volumes", []):
        if "csi" in vol and vol["csi"].get("driver") == "gcsfuse.csi.storage.gke.io":
            if "volumeAttributes" not in vol["csi"] or not vol["csi"]["volumeAttributes"]:
                vol["csi"]["volumeAttributes"] = {}
            vol["csi"]["volumeAttributes"]["bucketName"] = bucket_name
            if extra_flag:
                # Strip leading dashes for CSI mountOptions e.g. '--client-protocol=grpc' -> 'client-protocol=grpc'
                flag_str = extra_flag.strip().lstrip('-')
                vol["csi"]["volumeAttributes"]["mountOptions"] = flag_str

    container = pod_spec["containers"][0]
    container["image"] = image
    container["args"] = args
    
    return job_spec

def wait_for_job_completion(job_name, timeout_seconds=3600):
    """Waits for a Kubernetes Job to complete or fail."""
    print(f"Waiting for Job {job_name} to complete...")
    start_time = time.time()
    
    while True:
        if time.time() - start_time > timeout_seconds:
            print(f"--- Job {job_name} TIMED OUT (Script timeout reached) ---", file=sys.stderr)
            print("Fetching logs...", file=sys.stderr)
            subprocess.run(["kubectl", "logs", "-l", f"job-name={job_name}"])
            return False
            
        try:
            subprocess.run(
                ["kubectl", "wait", f"job/{job_name}", "--for=condition=complete", "--timeout=10s"],
                check=True, text=True, capture_output=True
            )
            print(f"--- Job {job_name} finished successfully ---")
            return True
        except subprocess.CalledProcessError as e:
            if e.stderr and "not found" in e.stderr.lower():
                print(f"--- Job {job_name} NOT FOUND ---", file=sys.stderr)
                return False
                
            # Check if the job has failed
            res_failed = subprocess.run(
                ["kubectl", "get", f"job/{job_name}", "-o", "jsonpath={.status.conditions[?(@.type=='Failed')].status}"],
                capture_output=True, text=True
            )
            if res_failed.stdout.strip() == "True":
                print(f"--- Job {job_name} FAILED ---", file=sys.stderr)
                
                # Get the failure reason
                res_reason = subprocess.run(
                    ["kubectl", "get", f"job/{job_name}", "-o", "jsonpath={.status.conditions[?(@.type=='Failed')].reason}"],
                    capture_output=True, text=True
                )
                reason = res_reason.stdout.strip()
                
                res_message = subprocess.run(
                    ["kubectl", "get", f"job/{job_name}", "-o", "jsonpath={.status.conditions[?(@.type=='Failed')].message}"],
                    capture_output=True, text=True
                )
                message = res_message.stdout.strip()
                
                if reason == "DeadlineExceeded":
                    print(f"Job timed out: {message}", file=sys.stderr)
                elif reason or message:
                    print(f"Reason: {reason} - {message}", file=sys.stderr)
                
                print("Fetching logs...", file=sys.stderr)
                subprocess.run(["kubectl", "logs", "-l", f"job-name={job_name}"])
                return False

def run_benchmark_job(job_name, image, args_list, project_id, dataset_id, table_id, bucket_name, service_account, extra_flag=None):
    """Runs a benchmark job on GKE and waits for its completion."""
    # 1. Cleanup any existing job with the same name
    subprocess.run(["kubectl", "delete", "job", job_name, "--ignore-not-found=true", "--wait=true"], capture_output=True)

    # 2. Create Job Spec and Apply
    job_spec = create_job_spec(job_name, image, args_list, bucket_name, service_account, extra_flag)
    yaml_data = yaml.dump(job_spec)

    print(f"--- Submitting Kubernetes Job: {job_name} ---")
    res = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml_data,
        capture_output=True,
        text=True
    )
    if res.returncode != 0:
        print(f"Failed to apply job {job_name}:\n{res.stderr}", file=sys.stderr)
        return False

    # 4. Wait for Job to Complete
    return wait_for_job_completion(job_name)

def main():
    parser = argparse.ArgumentParser(description="GKE benchmark runner for GCSFuse NPI.")
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket to use.")
    parser.add_argument("--kubernetes-service-account", default="default", help="Kubernetes Service Account name to run the job with. Default: default.")
    parser.add_argument("--dry-run", action="store_true", help="List down all the benchmarks that would be executed without actually running them.")
    parser.add_argument("--project-id", required=True, help="Project ID for results.")
    parser.add_argument("--bq-dataset-id", required=True, help="BigQuery dataset ID for results.")
    parser.add_argument("--iterations", type=int, default=5, help="Number of FIO test iterations per benchmark. Default: 5.")
    parser.add_argument("--cluster-name", help="GKE cluster name. If provided with --location, the script will fetch cluster credentials.")
    parser.add_argument("--location", help="GCP location (region or zone) of the GKE cluster.")
    parser.add_argument(
        "-b", "--benchmarks",
        nargs="+",
        default=["all"],
        help="Space-separated list of benchmarks to run (e.g., read_http1 write_grpc). Use 'all' to run all 4."
    )
    
    args = parser.parse_args()

    if args.cluster_name and args.location:
        print(f"--- Fetching credentials for GKE cluster: {args.cluster_name} in {args.location} ---")
        res = subprocess.run([
            "gcloud", "container", "clusters", "get-credentials", args.cluster_name,
            "--location", args.location, "--project", args.project_id
        ], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Failed to fetch cluster credentials:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
        print("Successfully fetched cluster credentials.")
    elif args.cluster_name or args.location:
        parser.error("Both --cluster-name and --location must be provided together to fetch cluster credentials.")

    all_benchmarks = [
        ("read", "http1", "fio-read-benchmark", ""),
        ("read", "grpc", "fio-read-benchmark", "--client-protocol=grpc"),
        ("write", "http1", "fio-write-benchmark", ""),
        ("write", "grpc", "fio-write-benchmark", "--client-protocol=grpc"),
    ]

    benchmarks_to_run = []
    if "all" in args.benchmarks:
        benchmarks_to_run = all_benchmarks
    else:
        available_names = {f"{b[0]}_{b[1]}" for b in all_benchmarks}
        for b in args.benchmarks:
            if b not in available_names:
                print(f"Error: Benchmark '{b}' not found. Available benchmarks are: {', '.join(available_names)}", file=sys.stderr)
                sys.exit(1)
        benchmarks_to_run = [b for b in all_benchmarks if f"{b[0]}_{b[1]}" in args.benchmarks]


    if args.dry_run:
        print("--- [DRY RUN] Benchmarks to be executed ---")
        for bench_type, config_name, _, _ in benchmarks_to_run:
            print(f" - {bench_type}_{config_name}")
        return

    start_time = datetime.datetime.now()
    print(f"--- Entire run started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    failed_benchmarks = []

    for bench_type, config_name, image_suffix, extra_flag in benchmarks_to_run:
        full_bench_name = f"{bench_type}_{config_name}"
        job_name = f"gcsfuse-npi-{full_bench_name}".replace("_", "-")
        bq_table_id = f"fio_{full_bench_name}"
        
        image = f"us-docker.pkg.dev/{args.project_id}/gcsfuse-benchmarks/{image_suffix}:latest"
        cmd_args = [
            f"--iterations={args.iterations}",
            f"--project-id={args.project_id}",
            f"--bq-dataset-id={args.bq_dataset_id}",
            f"--bq-table-id={bq_table_id}",
            "--mount-path=/data"
        ]

        success = run_benchmark_job(
            job_name=job_name,
            image=image,
            args_list=cmd_args,
            project_id=args.project_id,
            dataset_id=args.bq_dataset_id,
            table_id=bq_table_id,
            bucket_name=args.bucket_name,
            service_account=args.kubernetes_service_account,
            extra_flag=extra_flag
        )

        if not success:
            failed_benchmarks.append(full_bench_name)
            print(f"Skipping further benchmarks because {full_bench_name} failed.")
            break

        # Delete job after successful completion to clean up the cluster
        subprocess.run(["kubectl", "delete", "job", job_name, "--ignore-not-found=true", "--wait=true"], capture_output=True)

    if failed_benchmarks:
        print(f"\n--- Some benchmarks failed: {', '.join(failed_benchmarks)} ---", file=sys.stderr)
        sys.exit(1)
    else:
        print("\n--- All benchmarks completed successfully! ---")

    end_time = datetime.datetime.now()
    print(f"--- Entire run ended at: {end_time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    print(f"--- Total duration: {end_time - start_time} ---")

if __name__ == "__main__":
    main()
