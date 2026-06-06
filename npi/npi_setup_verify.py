#!/usr/bin/env python3
"""Automation script for setting up, running, and tearing down the Go gRPC DirectPath and MSS verification workload."""

import argparse
import subprocess
import sys
import os
import shutil
import time
import threading
import tempfile
import yaml

def run_cmd(cmd, check=True):
    print(f"\n[INFO] Running: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)

def build_images(project_id, gcsfuse_version):
    print("\n[INFO] Creating Artifact Registry repository...")
    run_cmd([
        "gcloud", "artifacts", "repositories", "create", "gcsfuse-benchmarks",
        "--repository-format=docker", "--location=us", f"--project={project_id}"
    ], check=False)

    cwd = os.path.dirname(os.path.realpath(__file__))
    print(f"\n[INFO] Building and pushing images in {cwd}...")
    subprocess.run(["make", f"PROJECT={project_id}", f"GCSFUSE_VERSION={gcsfuse_version}"], check=True, text=True, cwd=cwd)

def setup_global_infra(args):
    project_id = args.project_id
    region = args.region
    network_name = args.network_name
    subnet_name = args.subnet_name
    bucket_name = args.bucket_name or f"grpc-verify-bucket-{project_id}-{region}"
    
    ksa_name = "gcsfuse-npi-ksa"
    namespace = "default"

    # 1. Enable APIs
    run_cmd(["gcloud", "services", "enable", "artifactregistry.googleapis.com", "container.googleapis.com", f"--project={project_id}"])

    # 2. Create VPC Network
    run_cmd(["gcloud", "compute", "networks", "create", network_name, f"--project={project_id}", "--subnet-mode=custom", f"--mtu={args.mtu}"], check=False)

    # 3. Create Subnet (Dual-Stack)
    run_cmd([
        "gcloud", "compute", "networks", "subnets", "create", subnet_name,
        f"--project={project_id}", f"--network={network_name}", f"--region={region}",
        "--range=10.0.0.0/20", "--enable-private-ip-google-access",
        "--stack-type=IPV4_IPV6", "--ipv6-access-type=EXTERNAL"
    ], check=False)

    # 4. Create GCS Bucket
    run_cmd([
        "gcloud", "storage", "buckets", "create", f"gs://{bucket_name}",
        f"--project={project_id}", f"--location={region}",
        "--uniform-bucket-level-access", "--enable-hierarchical-namespace"
    ], check=False)

    # 5. Fetch Project Number
    res = subprocess.run(
        ["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"],
        capture_output=True, text=True, check=True
    )
    project_number = res.stdout.strip()

    # 6. Bind KSA Principal to GCS
    principal = f"principal://iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/{project_id}.svc.id.goog/subject/ns/{namespace}/sa/{ksa_name}"
    
    run_cmd([
        "gcloud", "storage", "buckets", "add-iam-policy-binding", f"gs://{bucket_name}",
        f"--member={principal}", "--role=roles/storage.objectAdmin"
    ])

def get_node_pool_status(project_id, zone, cluster_name, pool_name):
    cmd = [
        "gcloud", "container", "node-pools", "describe", pool_name,
        f"--cluster={cluster_name}", f"--zone={zone}", f"--project={project_id}",
        "--format=value(status)"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        return res.stdout.strip()
    return None

def create_tpu_node_pool_with_retries(args, cluster_name):
    project_id = args.project_id
    zone = args.zone
    tpu_machine = args.tpu_machine_type
    
    timeout_seconds = args.tpu_provision_timeout_hours * 3600
    start_time = time.time()
    
    retry_count = 0
    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            print(f"[ERROR] TPU node pool provisioning timed out after {args.tpu_provision_timeout_hours} hours.", file=sys.stderr)
            sys.exit(1)
            
        print(f"\n[INFO] [Attempt {retry_count + 1}] Creating TPU Node Pool...")
        
        create_cmd = [
            "gcloud", "container", "node-pools", "create", "tpu-pool",
            f"--cluster={cluster_name}", f"--project={project_id}", f"--zone={zone}",
            f"--node-locations={zone}", f"--machine-type={tpu_machine}", "--num-nodes=1"
        ]
        
        if args.reservation_affinity:
            create_cmd.append(f"--reservation-affinity={args.reservation_affinity}")
        if args.reservation:
            create_cmd.append(f"--reservation={args.reservation}")
            
        subprocess.run(create_cmd, text=True)
        
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                break
                
            status = get_node_pool_status(project_id, zone, cluster_name, "tpu-pool")
            print(f"[INFO] Current tpu-pool status: {status}")
            
            if status == "RUNNING":
                print("[INFO] TPU Node Pool successfully provisioned and RUNNING!")
                return
                
            elif status in ["PROVISIONING", "RECONCILING"]:
                time.sleep(30)
                
            else:
                print(f"[WARNING] Node pool creation failed with status: {status}")
                break
        
        print("[INFO] Cleaning up failed node pool before retrying...")
        subprocess.run([
            "gcloud", "container", "node-pools", "delete", "tpu-pool",
            f"--cluster={cluster_name}", f"--zone={zone}", f"--project={project_id}", "--quiet"
        ])
        
        retry_count += 1
        elapsed = time.time() - start_time
        if elapsed < timeout_seconds:
            sleep_time = min(300, timeout_seconds - elapsed)
            print(f"[INFO] Waiting {sleep_time:.0f} seconds before retrying...")
            time.sleep(sleep_time)

def setup_cluster_control_plane(args, cluster_name, gke_version):
    project_id = args.project_id
    zone = args.zone
    network_name = args.network_name
    subnet_name = args.subnet_name

    # Check if cluster already exists
    res = subprocess.run([
        "gcloud", "container", "clusters", "describe", cluster_name,
        f"--zone={zone}", f"--project={project_id}"
    ], capture_output=True)
    if res.returncode == 0:
        print(f"\n[INFO] Cluster {cluster_name} already exists. Deleting it to ensure clean slate...")
        delete_cluster(args, cluster_name)

    print(f"\n[INFO] Creating GKE Cluster (Control Plane): {cluster_name} (Version: {gke_version})...")
    run_cmd([
        "gcloud", "container", "clusters", "create", cluster_name,
        f"--project={project_id}", f"--zone={zone}", f"--cluster-version={gke_version}",
        f"--network={network_name}", f"--subnetwork={subnet_name}",
        "--machine-type=n2-standard-8", "--num-nodes=1",
        f"--workload-pool={project_id}.svc.id.goog",
        "--stack-type=ipv4-ipv6",
        "--enable-ip-alias",
        "--enable-dataplane-v2"
    ])

def provision_tpu_and_setup_ksa(args, cluster_name):
    project_id = args.project_id
    zone = args.zone
    ksa_name = "gcsfuse-npi-ksa"
    namespace = "default"

    # Add TPU Node Pool (with retries and reservation support)
    if not args.no_tpu:
        create_tpu_node_pool_with_retries(args, cluster_name)

    # Get Cluster Credentials
    run_cmd(["gcloud", "container", "clusters", "get-credentials", cluster_name, f"--zone={zone}", f"--project={project_id}"])

    # Create KSA
    run_cmd(["kubectl", "create", "serviceaccount", ksa_name, f"--namespace={namespace}"], check=False)

def parse_key_value_pairs(kv_str):
    if not kv_str:
        return None
    pairs = {}
    for part in kv_str.split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            pairs[k.strip()] = v.strip()
    return pairs

def create_job_spec(job_name, image, bucket_name, service_account, node_selector=None, resources_limits=None):
    script_dir = os.path.dirname(os.path.realpath(__file__))
    template_path = os.path.join(script_dir, "npi_job_spec.yaml")
        
    with open(template_path, 'r') as f:
        job_spec = yaml.safe_load(f)
        
    job_spec["metadata"]["name"] = job_name
    pod_spec = job_spec["spec"]["template"]["spec"]
    
    # Replace service account
    pod_spec["serviceAccountName"] = service_account
    
    if node_selector:
        pod_spec["nodeSelector"] = node_selector
        
    # Remove GCSFuse CSI volumes and annotations
    if "volumes" in pod_spec:
        pod_spec["volumes"] = [v for v in pod_spec["volumes"] if v["name"] != "gcsfuse-csi"]
    if "volumeMounts" in pod_spec["containers"][0]:
        pod_spec["containers"][0]["volumeMounts"] = [vm for vm in pod_spec["containers"][0]["volumeMounts"] if vm["name"] != "gcsfuse-csi"]
    if "annotations" in job_spec["spec"]["template"]["metadata"]:
        job_spec["spec"]["template"]["metadata"]["annotations"].pop("gke-gcsfuse/volumes", None)
    
    container = pod_spec["containers"][0]
    container["image"] = image
    container["args"] = [
        f"--bucket={bucket_name}",
        "--filesize=1M",
        "--nrfiles=1"
    ]
    
    if resources_limits:
        if container.get("resources") is None:
            container["resources"] = {}
        if container["resources"].get("limits") is None:
            container["resources"]["limits"] = {}
        container["resources"]["limits"].update(resources_limits)
    
    return job_spec

def run_verify_job(args, cluster_name):
    # Fetch credentials
    run_cmd(["gcloud", "container", "clusters", "get-credentials", cluster_name, f"--zone={args.zone}", f"--project={args.project_id}"])
    
    job_name = "gcsfuse-npi-go-grpc-verify"
    ksa_name = "gcsfuse-npi-ksa"
    bucket_name = args.bucket_name or f"grpc-verify-bucket-{args.project_id}-{args.region}"
    image = f"us-docker.pkg.dev/{args.project_id}/gcsfuse-benchmarks/go-grpc-verify:{args.image_version}"
    
    node_selector = None
    if not args.no_tpu:
        node_selector = parse_key_value_pairs(args.node_selector)
    elif args.node_selector != "cloud.google.com/gke-tpu-accelerator=tpu-v6e-slice,cloud.google.com/gke-tpu-topology=2x2":
        node_selector = parse_key_value_pairs(args.node_selector)
        
    resources_limits = None
    if not args.no_tpu:
        resources_limits = parse_key_value_pairs(args.resources_limits)
    elif args.resources_limits != "google.com/tpu=4":
        resources_limits = parse_key_value_pairs(args.resources_limits)
    
    # 1. Cleanup any existing job
    subprocess.run(["kubectl", "delete", "job", job_name, "--ignore-not-found=true", "--wait=true"], capture_output=True)
    
    # 2. Build Job Spec
    job_spec = create_job_spec(job_name, image, bucket_name, ksa_name, node_selector, resources_limits)
    yaml_data = yaml.dump(job_spec)
    
    print(f"\n[INFO] Submitting Kubernetes Job: {job_name} ...")
    res = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml_data,
        capture_output=True,
        text=True
    )
    if res.returncode != 0:
        print(f"[ERROR] Failed to apply job {job_name}:\n{res.stderr}", file=sys.stderr)
        sys.exit(1)
        
    # 3. Wait for job to finish
    print(f"[INFO] Waiting for Job {job_name} to complete...")
    succeeded = False
    for _ in range(60): # timeout after 5 minutes
        res_succeeded = subprocess.run(
            ["kubectl", "get", f"job/{job_name}", "-o", "jsonpath={.status.succeeded}"],
            capture_output=True, text=True
        )
        if res_succeeded.stdout.strip() == "1":
            succeeded = True
            print(f"--- Job {job_name} finished successfully ---")
            break
            
        res_failed = subprocess.run(
            ["kubectl", "get", f"job/{job_name}", "-o", "jsonpath={.status.conditions[?(@.type=='Failed')].status}"],
            capture_output=True, text=True
        )
        if res_failed.stdout.strip() == "True":
            print(f"--- Job {job_name} FAILED ---", file=sys.stderr)
            break
            
        time.sleep(5)
        
    # 4. Fetch and print logs
    print("\n" + "="*50)
    print("===             JOB OUTPUT LOGS              ===")
    print("="*50)
    pod_res = subprocess.run(
        ["kubectl", "get", "pods", "-l", f"job-name={job_name}", "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True
    )
    pod_name = pod_res.stdout.strip()
    if pod_name:
        subprocess.run(["kubectl", "logs", pod_name])
    else:
        print(f"No pod found for job {job_name}", file=sys.stderr)
    print("="*50 + "\n")
    
    if not succeeded:
        sys.exit(1)

def delete_cluster(args, cluster_name):
    run_cmd([
        "gcloud", "container", "clusters", "delete", cluster_name,
        f"--zone={args.zone}", f"--project={args.project_id}", "--quiet"
    ], check=False)

def cleanup(args):
    project_id = args.project_id
    region = args.region
    network_name = args.network_name
    subnet_name = args.subnet_name
    bucket_name = args.bucket_name or f"grpc-verify-bucket-{project_id}-{region}"
    
    # 1. Delete cluster
    print("\n[INFO] Starting GKE cluster deletion...")
    delete_cluster(args, args.cluster_name)

    # 2. Delete GCS Bucket
    run_cmd(["gcloud", "storage", "rm", "-r", f"gs://{bucket_name}", f"--project={project_id}", "--quiet"], check=False)

    # 3. Delete Subnet & VPC Network
    run_cmd(["gcloud", "compute", "networks", "subnets", "delete", subnet_name, f"--region={region}", f"--project={project_id}", "--quiet"], check=False)
    run_cmd(["gcloud", "compute", "networks", "delete", network_name, f"--project={project_id}", "--quiet"], check=False)

    print("\n--- Cleanup completed! ---")

def main():
    parser = argparse.ArgumentParser(description="DirectPath and MSS Verification setup/cleanup automation.")
    parser.add_argument("--project-id", required=True, help="GCP Project ID.")
    parser.add_argument("--cluster-name", default="grpc-verify-cluster", help="Name of GKE cluster.")
    parser.add_argument("--bucket-name", default=None, help="Name of GCS Bucket (default: grpc-verify-bucket-<project-id>-<region>).")
    parser.add_argument("--network-name", default="grpc-verify-net", help="VPC Network name.")
    parser.add_argument("--subnet-name", default="grpc-verify-subnet", help="Subnet name.")
    parser.add_argument("--region", default="europe-west4", help="GCP region.")
    parser.add_argument("--zone", default="europe-west4-a", help="GCP zone.")
    parser.add_argument("--gke-version", default="1.35.3-gke.2190000", help="GKE version.")
    parser.add_argument("--tpu-machine-type", default="ct6e-standard-4t", help="TPU machine type for node pool.")
    parser.add_argument("--gcsfuse-version", default="master", help="GCSFuse branch/version to build images for.")
    parser.add_argument("--keep-cluster", action="store_true", help="Keep cluster alive after run (do not delete it).")
    parser.add_argument("--mtu", type=int, default=8896, help="VPC Network MTU.")
    parser.add_argument("--tpu-provision-timeout-hours", type=float, default=2.0, help="TPU node pool provisioning timeout in hours.")
    parser.add_argument("--reservation-affinity", default=None, choices=["any", "none", "specific"], help="GCE Reservation affinity for TPU node pool.")
    parser.add_argument("--reservation", default=None, help="Name of GCE reservation to use.")
    
    # TPU defaults for node-selector and resource limits
    parser.add_argument(
        "--node-selector",
        default="cloud.google.com/gke-tpu-accelerator=tpu-v6e-slice,cloud.google.com/gke-tpu-topology=2x2",
        help="Comma-separated list of key-value pairs for pod nodeSelector"
    )
    parser.add_argument(
        "--resources-limits",
        default="google.com/tpu=4",
        help="Comma-separated list of key-value pairs for resource limits"
    )
    parser.add_argument("--image-version", default="latest", help="The version (tag) of the Docker images.")
    parser.add_argument("--no-tpu", action="store_true", help="Deploy the workload on standard GKE nodes instead of TPU nodes.")

    subparsers = parser.add_subparsers(dest="action", required=True, help="Action to perform.")
    subparsers.add_parser("setup-global", help="Setup VPC Network, Bucket, and IAM permissions.")
    subparsers.add_parser("build-images", help="Clone gcsfuse-tools and build/push benchmark images.")
    subparsers.add_parser("run-verify", help="Setup GKE control plane, TPU node pool, deploy job, and fetch logs.")
    subparsers.add_parser("run-all", help="Run setup-global, build-images, and run-verify in one go.")
    subparsers.add_parser("cleanup", help="Tear down GKE cluster, bucket, and VPC network.")
    
    args = parser.parse_args()

    if args.reservation_affinity == "specific" and not args.reservation:
        parser.error("--reservation is required when --reservation-affinity is set to specific.")

    def run_verify_sequence(args):
        try:
            setup_cluster_control_plane(args, args.cluster_name, args.gke_version)
            provision_tpu_and_setup_ksa(args, args.cluster_name)
            run_verify_job(args, args.cluster_name)
        finally:
            if not args.keep_cluster:
                print("[INFO] Tearing down cluster...")
                delete_cluster(args, args.cluster_name)

    if args.action == "setup-global":
        setup_global_infra(args)
    elif args.action == "build-images":
        build_images(args.project_id, args.gcsfuse_version)
    elif args.action == "run-verify":
        run_verify_sequence(args)
    elif args.action == "run-all":
        setup_global_infra(args)
        build_images(args.project_id, args.gcsfuse_version)
        run_verify_sequence(args)
    elif args.action == "cleanup":
        cleanup(args)

if __name__ == "__main__":
    main()
