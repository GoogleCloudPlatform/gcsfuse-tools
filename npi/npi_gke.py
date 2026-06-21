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

def create_job_spec(job_name, image, args, bucket_name, service_account, extra_flag=None, use_memory_volumes=False, is_go_client=False, node_selector=None, resources_limits=None, project_id=None):
    """Creates a Kubernetes Job spec dictionary from the template yaml."""
    script_dir = os.path.dirname(os.path.realpath(__file__))
    template_path = os.path.join(script_dir, "npi_job_spec.yaml")
    
    with open(template_path, 'r') as f:
        job_spec = yaml.safe_load(f)
        
    job_spec["metadata"]["name"] = job_name
    
    pod_spec = job_spec["spec"]["template"]["spec"]
    
    # Replace service account
    pod_spec["serviceAccountName"] = service_account
    
    print(f"DEBUG: Parsed node_selector: {node_selector}")
    if node_selector:
        pod_spec["nodeSelector"] = node_selector
        print(f"DEBUG: Applied nodeSelector: {pod_spec['nodeSelector']}")
        
    if is_go_client:
        # For Go client, remove the GCSFuse CSI volumes and annotations
        if "volumes" in pod_spec:
            pod_spec["volumes"] = [v for v in pod_spec["volumes"] if v["name"] != "gcsfuse-csi"]
        if "volumeMounts" in pod_spec["containers"][0]:
            pod_spec["containers"][0]["volumeMounts"] = [vm for vm in pod_spec["containers"][0]["volumeMounts"] if vm["name"] != "gcsfuse-csi"]
        if "annotations" in job_spec["spec"]["template"]["metadata"]:
            job_spec["spec"]["template"]["metadata"]["annotations"].pop("gke-gcsfuse/volumes", None)
    else:
        # Replace bucket name in CSI volume
        for vol in pod_spec.get("volumes", []):
            if "csi" in vol and vol["csi"].get("driver") == "gcsfuse.csi.storage.gke.io":
                if "volumeAttributes" not in vol["csi"] or not vol["csi"]["volumeAttributes"]:
                    vol["csi"]["volumeAttributes"] = {}
                vol["csi"]["volumeAttributes"]["bucketName"] = bucket_name
                
                mount_opts = []
                if extra_flag:
                    # extra_flag can be a comma-separated list of flags, e.g., "implicit-dirs,billing-project=xyz"
                    for opt in extra_flag.split(","):
                        opt_str = opt.strip().lstrip('-')
                        if opt_str:
                            if '=' in opt_str:
                                k, v = opt_str.split('=', 1)
                                opt_str = f"{k.strip()}={v.strip()}"
                            mount_opts.append(opt_str)
                
                if mount_opts:
                    vol["csi"]["volumeAttributes"]["mountOptions"] = ",".join(mount_opts)


        if use_memory_volumes:
            if "volumes" not in pod_spec:
                pod_spec["volumes"] = []
            pod_spec["volumes"].extend([
                {
                    "name": "gke-gcsfuse-cache",
                    "emptyDir": {
                        "medium": "Memory"
                    }
                },
                {
                    "name": "gke-gcsfuse-buffer",
                    "emptyDir": {
                        "medium": "Memory"
                    }
                }
            ])
    
    container = pod_spec["containers"][0]
    container["image"] = image
    container["args"] = args
    
    if resources_limits:
        if container.get("resources") is None:
            container["resources"] = {}
        if container["resources"].get("limits") is None:
            container["resources"]["limits"] = {}
        container["resources"]["limits"].update(resources_limits)
    
    return job_spec


def wait_for_job_completion(job_name, timeout_seconds=None):
    """Waits for a Kubernetes Job to complete, streaming its pod logs in real-time."""
    print(f"Waiting for Job {job_name} to start...")
    start_time = time.time()
    
    # 1. Wait for pod to reach Running or Terminal phase
    pod_started = False
    last_print_time = 0
    while True:
        if timeout_seconds is not None and time.time() - start_time > timeout_seconds:
            print(f"--- Job {job_name} TIMED OUT waiting to start ---", file=sys.stderr)
            return False
            
        res = subprocess.run(
            ["kubectl", "get", "pods", "-l", f"job-name={job_name}", "-o", "jsonpath={.items[*].status.phase}"],
            capture_output=True, text=True
        )
        phases = res.stdout.strip().split()
        if "Running" in phases or "Succeeded" in phases or "Failed" in phases:
            pod_started = True
            break
            
        if time.time() - last_print_time > 30:
            last_print_time = time.time()
            print(f"[{job_name}] Pod phases: {', '.join(phases) if phases else 'None (creating...)'}. Waiting for pod to start...")
            sys.stdout.flush()
            
            # Fetch recent scheduling failure reason if any
            events_res = subprocess.run(
                ["kubectl", "get", "events", "--field-selector", "reason=FailedScheduling", "-o", "jsonpath={.items[-1].message}"],
                capture_output=True, text=True
            )
            failed_reason = events_res.stdout.strip()
            if failed_reason:
                print(f"[{job_name}] Warning (Scheduling): {failed_reason}")
                sys.stdout.flush()
        
        time.sleep(2)
        
    print(f"--- Job {job_name} pod started, streaming logs ---")
    
    # 2. Stream logs from the pod container 'benchmark'
    log_proc = subprocess.Popen(
        ["kubectl", "logs", "-f", "-l", f"job-name={job_name}", "-c", "benchmark"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    
    while True:
        line = log_proc.stdout.readline()
        if not line:
            break
        print(line.strip())
        sys.stdout.flush()
        
    log_proc.wait()
    
    # 3. Wait up to 15 seconds for Job object to finalize state
    wait_res = subprocess.run(
        ["kubectl", "wait", f"job/{job_name}", "--for=condition=complete", "--timeout=15s"],
        capture_output=True
    )
    if wait_res.returncode == 0:
        print(f"--- Job {job_name} finished successfully ---")
        return True
    else:
        print(f"--- Job {job_name} FAILED or timed out finalizing ---", file=sys.stderr)
        return False


def run_benchmark_job(job_name, image, args_list, project_id, dataset_id, table_id, bucket_name, service_account, extra_flag=None, use_memory_volumes=False, is_go_client=False, node_selector=None, resources_limits=None):
    """Runs a benchmark job on GKE and waits for its completion."""
    # 1. Cleanup any existing job with the same name
    subprocess.run(["kubectl", "delete", "job", job_name, "--ignore-not-found=true", "--wait=true"], capture_output=True)

    # 2. Create Job Spec and Apply
    job_spec = create_job_spec(job_name, image, args_list, bucket_name, service_account, extra_flag, use_memory_volumes, is_go_client, node_selector, resources_limits, project_id=project_id)
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

def parse_key_value_pairs(kv_str):
    if not kv_str:
        return None
    pairs = {}
    for part in kv_str.split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            pairs[k.strip()] = v.strip()
    return pairs
def setup_kubernetes_service_account(project_id, ksa_name, namespace, buckets, dry_run=False):
    """Creates a GKE service account and grants it Workload Identity access to GCS and BigQuery."""
    if dry_run:
        print(f"[DRY RUN] Would verify/create Kubernetes Service Account '{ksa_name}' in namespace '{namespace}'")
        return True

    # 1. Create KSA if not exists
    print(f"--- Ensuring Kubernetes Service Account '{ksa_name}' exists in namespace '{namespace}' ---")
    create_cmd = ["kubectl", "create", "serviceaccount", ksa_name, f"--namespace={namespace}"]
    res = subprocess.run(create_cmd, capture_output=True, text=True)
    if res.returncode != 0 and "already exists" not in res.stderr:
        print(f"Failed to create Kubernetes service account: {res.stderr}", file=sys.stderr)
        return False
        
    # 2. Get GCP project number
    num_cmd = ["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"]
    res = subprocess.run(num_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Failed to retrieve project number for {project_id}: {res.stderr}", file=sys.stderr)
        return False
    project_number = res.stdout.strip()
    
    member_principal = f"principal://iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/{project_id}.svc.id.goog/subject/ns/{namespace}/sa/{ksa_name}"
    
    # 3. Grant roles/storage.objectUser on each GCS bucket
    for b in buckets:
        if not b:
            continue
        b_name = b[5:] if b.startswith("gs://") else b
        print(f"--- Granting storage.admin role to {ksa_name} on bucket gs://{b_name} ---")
        iam_cmd = [
            "gcloud", "storage", "buckets", "add-iam-policy-binding", f"gs://{b_name}",
            f"--member={member_principal}", "--role=roles/storage.admin", "--quiet"
        ]
        res = subprocess.run(iam_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Failed to bind storage permission on gs://{b_name}: {res.stderr}", file=sys.stderr)
            return False

    # 4. Grant roles/bigquery.dataEditor on the GCP project (for dataset tables)
    print(f"--- Granting bigquery.dataEditor role to {ksa_name} on project {project_id} ---")
    bq_cmd = [
        "gcloud", "projects", "add-iam-policy-binding", project_id,
        f"--member={member_principal}", "--role=roles/bigquery.dataEditor", "--condition=None", "--quiet"
    ]
    res = subprocess.run(bq_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Failed to bind BigQuery permission on project {project_id}: {res.stderr}", file=sys.stderr)
        return False
        
    return True


def main():
    parser = argparse.ArgumentParser(description="GKE benchmark runner for GCSFuse NPI.")
    parser.add_argument("--bucket-name", required=True, help="Name of the GCS bucket to use.")
    parser.add_argument("--kubernetes-service-account", default="gcsfuse-npi-ksa", help="Kubernetes Service Account name to run the job with. Default: gcsfuse-npi-ksa.")
    parser.add_argument("--dry-run", action="store_true", help="List down all the benchmarks that would be executed without actually running them.")
    parser.add_argument("--project-id", required=True, help="Project ID for results.")
    parser.add_argument("--bq-dataset-id", required=True, help="BigQuery dataset ID for results.")
    parser.add_argument("--iterations", type=int, default=5, help="Number of FIO test iterations per benchmark. Default: 5.")
    parser.add_argument("--image-version", default="latest", help="The version (tag) of the benchmark Docker images to use. Default: latest.")
    parser.add_argument("--cluster-name", help="GKE cluster name. If provided with --location, the script will fetch cluster credentials.")
    parser.add_argument("--location", help="GCP location (region or zone) of the GKE cluster.")
    parser.add_argument(
        "-b", "--benchmarks",
        nargs="+",
        default=["all"],
        help="Space-separated list of benchmarks to run (e.g., read_http1 write_grpc). Use 'all' to run all 4."
    )
    parser.add_argument(
        "--run-file-cache-test",
        action="store_true",
        help="Run the file-cache benchmark in GKE. If not specified, file-cache tests are excluded."
    )
    parser.add_argument(
        "--file-cache-size-mb",
        type=int,
        default=2097152,
        help="The size of the file cache in MB. Default: 2097152."
    )
    parser.add_argument(
        "--is-rapid-bucket",
        action="store_true",
        help="If set, indicates that the bucket is a RAPID bucket. Only gRPC benchmarks will be run."
    )
    
    parser.add_argument(
        "--use-memory-volumes",
        action="store_true",
        help="Declare gke-gcsfuse-cache and gke-gcsfuse-buffer volumes on memory."
    )
    parser.add_argument(
        "--node-selector",
        default=None,
        help="Comma-separated list of key-value pairs for pod nodeSelector, e.g., 'key1=val1,key2=val2'"
    )
    parser.add_argument(
        "--resources-limits",
        default=None,
        help="Comma-separated list of key-value pairs for resource limits, e.g., 'google.com/tpu=4'"
    )
    
    args = parser.parse_args()

    node_selector = parse_key_value_pairs(args.node_selector)
    resources_limits = parse_key_value_pairs(args.resources_limits)

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

    # Automatically setup/ensure service account permissions
    buckets_to_auth = [args.bucket_name]
    if not setup_kubernetes_service_account(args.project_id, args.kubernetes_service_account, "default", buckets_to_auth, dry_run=args.dry_run):
        print("Failed to setup Kubernetes service account. Exiting.", file=sys.stderr)
        sys.exit(1)

    all_benchmarks = [
        ("host_info", "", "host-info-collector", "", 1, None),
        ("read", "http1", "fio-read-benchmark", "", None, None),
        ("read", "grpc", "fio-read-benchmark", "--client-protocol=http1", None, None),
        ("write", "http1", "fio-write-benchmark", "", None, None),
        ("write", "grpc", "fio-write-benchmark", "--client-protocol=http1", None, None),
        ("read_file_cache", "grpc", "fio-read-benchmark", f"client-protocol=grpc,metadata-cache-ttl-secs=-1,file-cache:max-size-mb:{args.file_cache_size_mb},file-cache-cache-file-for-range-read", 5, "--keep-mount"),
        ("go_read", "http1", "go-client-read-benchmark", "", None, "--client-protocol=http1"),
        ("go_read", "grpc", "go-client-read-benchmark", "", None, "--client-protocol=grpc"),
    ]

    # If not explicitly told to run file cache benchmarks, validate and filter them out.
    if not args.run_file_cache_test:
        for b in args.benchmarks:
            if "file_cache" in b:
                parser.error(f"Benchmark '{b}' requires --run-file-cache-test flag.")

    benchmarks_to_run = []
    if "all" in args.benchmarks:
        if args.run_file_cache_test:
            benchmarks_to_run = all_benchmarks
        else:
            # Exclude file cache tests from 'all' when the flag is not passed.
            print("Warning: File-cache tests are not being run because --run-file-cache-test was not provided.", file=sys.stderr)
            # Also exclude go_read benchmarks from "all" if the user wants "all" FIO benchmarks.
            # Wait, should we include go_read benchmarks in "all"? Yes, "all" typically means all defined benchmarks.
            benchmarks_to_run = [b for b in all_benchmarks if "file_cache" not in b[0]]
    else:
        available_names = {f"{b[0]}_{b[1]}" if b[1] else b[0] for b in all_benchmarks}
        for b in args.benchmarks:
            if b not in available_names:
                print(f"Error: Benchmark '{b}' not found. Available benchmarks are: {', '.join(available_names)}", file=sys.stderr)
                sys.exit(1)
        benchmarks_to_run = [b for b in all_benchmarks if (f"{b[0]}_{b[1]}" if b[1] else b[0]) in args.benchmarks]

    if args.is_rapid_bucket:
        if "all" not in args.benchmarks:
            for b in args.benchmarks:
                if "http1" in b:
                    parser.error(f"Benchmark '{b}' is not supported for RAPID buckets (only gRPC benchmarks are allowed).")
        benchmarks_to_run = [b for b in benchmarks_to_run if "http1" not in b[1]]


    if args.dry_run:
        print("--- [DRY RUN] Benchmarks to be executed ---")

    start_time = datetime.datetime.now()
    if not args.dry_run:
        print(f"--- Entire run started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    failed_benchmarks = []

    for bench_type, config_name, image_suffix, extra_flag, iter_override, runner_args in benchmarks_to_run:
        full_bench_name = f"{bench_type}_{config_name}" if config_name else bench_type
        job_name = f"gcsfuse-npi-{full_bench_name}".replace("_", "-")
        is_go_client = (bench_type == "go_read")
        
        if bench_type == "host_info":
            bq_table_id = "host_info"
        elif bench_type == "read_file_cache":
            bq_table_id = "fio_read_file_cache"
        elif bench_type == "go_read":
            bq_table_id = f"go_client_read_{config_name}"
        else:
            bq_table_id = f"fio_{full_bench_name}"
        
        target_iterations = iter_override if iter_override is not None else args.iterations
        image = f"us-docker.pkg.dev/{args.project_id}/gcsfuse-benchmarks/{image_suffix}:{args.image_version}"
        
        if bench_type == "host_info":
            cmd_args = [
                f"--project-id={args.project_id}",
                f"--bq-dataset-id={args.bq_dataset_id}",
                f"--bq-table-id={bq_table_id}"
            ]
        elif is_go_client:
            cmd_args = [
                f"--iterations={target_iterations}",
                f"--project-id={args.project_id}",
                f"--bq-dataset-id={args.bq_dataset_id}",
                f"--bq-table-id={bq_table_id}",
                f"--bucket-name={args.bucket_name}"
            ]
        else:
            cmd_args = [
                f"--iterations={target_iterations}",
                f"--project-id={args.project_id}",
                f"--bq-dataset-id={args.bq_dataset_id}",
                f"--bq-table-id={bq_table_id}",
                "--mount-path=/data"
            ]
            
        if runner_args:
            cmd_args.append(runner_args)

        if args.dry_run:
            job_spec = create_job_spec(job_name, image, cmd_args, args.bucket_name, args.kubernetes_service_account, extra_flag, args.use_memory_volumes, is_go_client, node_selector, resources_limits)
            print(f" - {full_bench_name}")
            print(f"   Job Name: {job_name}")
            print(f"   Image: {image}")
            print(f"   Args: {cmd_args}")
            print(f"   Volumes: {[v['name'] for v in job_spec['spec']['template']['spec'].get('volumes', [])]}")
            if 'nodeSelector' in job_spec['spec']['template']['spec']:
                print(f"   NodeSelector: {job_spec['spec']['template']['spec']['nodeSelector']}")
            if 'resources' in job_spec['spec']['template']['spec']['containers'][0]:
                print(f"   Resources: {job_spec['spec']['template']['spec']['containers'][0]['resources']}")
        else:
            success = run_benchmark_job(
                job_name=job_name,
                image=image,
                args_list=cmd_args,
                project_id=args.project_id,
                dataset_id=args.bq_dataset_id,
                table_id=bq_table_id,
                bucket_name=args.bucket_name,
                service_account=args.kubernetes_service_account,
                extra_flag=extra_flag,
                use_memory_volumes=args.use_memory_volumes,
                is_go_client=is_go_client,
                node_selector=node_selector,
                resources_limits=resources_limits
            )

            if not success:
                failed_benchmarks.append(full_bench_name)
                print(f"Benchmark {full_bench_name} failed, but continuing with others.")

    if args.dry_run:
        return

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
