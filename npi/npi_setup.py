#!/usr/bin/env python3
"""Automation script for setting up, running, and tearing down NPI benchmarks on GKE."""

import argparse
import subprocess
import sys
import os

LRO_DAEMONSET_YAML = """
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: enable-lro
  namespace: kube-system
  labels:
    app: enable-lro
spec:
  selector:
    matchLabels:
      app: enable-lro
  template:
    metadata:
      labels:
        app: enable-lro
    spec:
      hostNetwork: true
      hostPID: true
      tolerations:
      - operator: "Exists"
      containers:
      - name: ethtool
        image: alpine
        securityContext:
          privileged: true
        volumeMounts:
        - name: host-proc
          mountPath: /host-proc
        command: ["/bin/sh", "-c"]
        args:
        - |
          apk update && apk add ethtool
          echo "Enabling LRO on eth0"
          nsenter --net=/host-proc/1/ns/net ethtool -K eth0 lro on || true
          nsenter --net=/host-proc/1/ns/net ethtool -k eth0 | grep large-receive-offload
          tail -f /dev/null
      volumes:
      - name: host-proc
        hostPath:
          path: /proc
"""

def run_cmd(cmd, check=True, cwd=None):
    print(f"\n[INFO] Running: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, cwd=cwd)

def clone_repo(branch="master"):
    repo_dir = "gcsfuse-tools"
    if not os.path.exists(repo_dir):
        print(f"[INFO] Cloning gcsfuse-tools repository (branch: {branch})...")
        run_cmd(["git", "clone", "-b", branch, "https://github.com/GoogleCloudPlatform/gcsfuse-tools.git", repo_dir])
    else:
        print(f"[INFO] Repository gcsfuse-tools already exists. Fetching latest changes...")
        run_cmd(["git", "fetch", "--all"], cwd=repo_dir)
        run_cmd(["git", "reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
    return repo_dir

def build_images(args):
    project_id = args.project_id
    repo_dir = clone_repo(args.repo_branch)

    # Ensure Artifact Registry repository exists in 'us' region (hardcoded in npi_gke.py)
    print(f"[INFO] Ensuring Artifact Registry repository 'gcsfuse-benchmarks' exists in 'us' location...")
    run_cmd([
        "gcloud", "artifacts", "repositories", "create", "gcsfuse-benchmarks",
        "--repository-format=docker", "--location=us", f"--project={project_id}"
    ], check=False)

    print(f"[INFO] Building benchmark images using build_images.py...")
    # Run from within gcsfuse-tools/npi directory
    run_cmd([
        "python3", "build_images.py",
        "--registry=us-docker.pkg.dev",
        f"--project={project_id}",
        "--image-version=latest"
    ], cwd=os.path.join(repo_dir, "npi"))

def setup_infra(args):
    project_id = args.project_id
    region = args.region
    zone = args.zone
    network_name = args.network_name
    subnet_name = args.subnet_name
    bucket_name = args.bucket_name or f"npi-benchmark-bucket-{project_id}"
    cluster_name = args.cluster_name
    gke_version = args.gke_version
    tpu_machine = args.tpu_machine_type
    
    ksa_name = "gcsfuse-npi-ksa"
    namespace = "default"

    # Clone/update repo to ensure we have it
    clone_repo(args.repo_branch)

    # 1. Enable APIs
    run_cmd(["gcloud", "services", "enable", "artifactregistry.googleapis.com", "container.googleapis.com", f"--project={project_id}"])

    # 2. Create VPC Network
    run_cmd(["gcloud", "compute", "networks", "create", network_name, f"--project={project_id}", "--subnet-mode=custom"], check=False)

    # 3. Create Subnet
    run_cmd([
        "gcloud", "compute", "networks", "subnets", "create", subnet_name,
        f"--project={project_id}", f"--network={network_name}", f"--region={region}",
        "--range=10.0.0.0/20", "--enable-private-ip-google-access"
    ], check=False)

    # 4. Create GCS Bucket
    run_cmd([
        "gcloud", "storage", "buckets", "create", f"gs://{bucket_name}",
        f"--project={project_id}", f"--location={region}",
        "--uniform-bucket-level-access", "--enable-hierarchical-namespace"
    ], check=False)

    # 5. Create GKE Cluster
    run_cmd([
        "gcloud", "container", "clusters", "create", cluster_name,
        f"--project={project_id}", f"--zone={zone}", f"--cluster-version={gke_version}",
        f"--network={network_name}", f"--subnet={subnet_name}",
        "--machine-type=n2-standard-8", "--num-nodes=1",
        f"--workload-pool={project_id}.svc.id.goog"
    ])

    # 6. Add TPU Node Pool
    run_cmd([
        "gcloud", "container", "node-pools", "create", "tpu-pool",
        f"--cluster={cluster_name}", f"--project={project_id}", f"--zone={zone}",
        f"--node-locations={zone}", f"--machine-type={tpu_machine}", "--num-nodes=1"
    ])

    # 7. Fetch Project Number for Workload Identity Principal
    res = subprocess.run(
        ["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"],
        capture_output=True, text=True, check=True
    )
    project_number = res.stdout.strip()

    # 8. Get Cluster Credentials
    run_cmd(["gcloud", "container", "clusters", "get-credentials", cluster_name, f"--zone={zone}", f"--project={project_id}"])

    # 9. Create Kubernetes Service Account (KSA)
    run_cmd(["kubectl", "create", "serviceaccount", ksa_name, f"--namespace={namespace}"], check=False)

    # 10. Bind KSA Workload Identity Principal directly to GCS Bucket and BigQuery
    principal = f"principal://iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/{project_id}.svc.id.goog/subject/ns/{namespace}/sa/{ksa_name}"
    
    # GCS Bucket binding
    run_cmd([
        "gcloud", "storage", "buckets", "add-iam-policy-binding", f"gs://{bucket_name}",
        f"--member={principal}", "--role=roles/storage.objectAdmin"
    ])

    # BigQuery bindings (Project level)
    for role in ["roles/bigquery.dataEditor", "roles/bigquery.jobUser"]:
        run_cmd([
            "gcloud", "projects", "add-iam-policy-binding", project_id,
            f"--member={principal}", f"--role={role}"
        ])

    # 11. Deploy LRO DaemonSet
    print("\n[INFO] Deploying LRO DaemonSet...")
    proc = subprocess.Popen(["kubectl", "apply", "-f", "-"], stdin=subprocess.PIPE, text=True)
    proc.communicate(input=LRO_DAEMONSET_YAML)
    if proc.returncode != 0:
        print("[ERROR] Failed to deploy LRO DaemonSet", file=sys.stderr)
        sys.exit(1)
        
    print("\n--- Setup completed successfully! ---")
    print(f"Bucket: gs://{bucket_name}")
    print(f"Service Account (KSA): {ksa_name}")
    print(f"Cluster: {cluster_name} in {zone}")

def run_benchmarks(args):
    project_id = args.project_id
    bucket_name = args.bucket_name or f"npi-benchmark-bucket-{project_id}"
    cluster_name = args.cluster_name
    zone = args.zone
    dataset_id = args.bq_dataset_id
    ksa_name = "gcsfuse-npi-ksa"

    if not dataset_id:
        print("[ERROR] --bq-dataset-id is required to run benchmarks.", file=sys.stderr)
        sys.exit(1)

    repo_dir = "gcsfuse-tools"
    if not os.path.exists(repo_dir):
        print("[ERROR] Repository not found. Run setup or build-images first.", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Executing benchmarks via npi_gke.py...")
    run_cmd([
        "python3", "npi_gke.py",
        f"--bucket-name={bucket_name}",
        f"--project-id={project_id}",
        f"--bq-dataset-id={dataset_id}",
        f"--kubernetes-service-account={ksa_name}",
        f"--cluster-name={cluster_name}",
        f"--location={zone}",
        "--node-selector=cloud.google.com/gke-tpu-accelerator=tpu-v6e-slice",
        "--resources-limits=google.com/tpu=4"
    ], cwd=os.path.join(repo_dir, "npi"))

def cleanup(args):
    project_id = args.project_id
    region = args.region
    zone = args.zone
    network_name = args.network_name
    subnet_name = args.subnet_name
    bucket_name = args.bucket_name or f"npi-benchmark-bucket-{project_id}"
    cluster_name = args.cluster_name

    # 1. Delete cluster
    run_cmd(["gcloud", "container", "clusters", "delete", cluster_name, f"--zone={zone}", f"--project={project_id}", "--quiet"], check=False)

    # 2. Delete GCS Bucket
    run_cmd(["gcloud", "storage", "buckets", "delete", f"gs://{bucket_name}", f"--project={project_id}", "--quiet"], check=False)

    # 3. Delete Subnet & VPC Network
    run_cmd(["gcloud", "compute", "networks", "subnets", "delete", subnet_name, f"--region={region}", f"--project={project_id}", "--quiet"], check=False)
    run_cmd(["gcloud", "compute", "networks", "delete", network_name, f"--project={project_id}", "--quiet"], check=False)

    print("\n--- Cleanup completed! ---")

def main():
    parser = argparse.ArgumentParser(description="NPI Benchmark GKE Cluster automation script.")
    parser.add_argument("--project-id", required=True, help="GCP Project ID.")
    parser.add_argument("--cluster-name", default="npi-benchmark-cluster", help="Name of GKE cluster.")
    parser.add_argument("--bucket-name", default=None, help="Name of GCS Bucket (default: npi-benchmark-bucket-<project-id>).")
    parser.add_argument("--network-name", default="npi-benchmark-net", help="VPC Network name.")
    parser.add_argument("--subnet-name", default="npi-benchmark-subnet", help="Subnet name.")
    parser.add_argument("--region", default="europe-west4", help="GCP region.")
    parser.add_argument("--zone", default="europe-west4-a", help="GCP zone.")
    parser.add_argument("--gke-version", default="1.35.3-gke.2190000", help="GKE version for cluster.")
    parser.add_argument("--tpu-machine-type", default="ct6e-standard-4t", help="TPU machine type for node pool.")
    parser.add_argument("--repo-branch", default="master", help="gcsfuse-tools repository branch to clone/use.")
    parser.add_argument("--bq-dataset-id", default=None, help="BigQuery dataset ID (required for 'run' and 'run-all').")

    subparsers = parser.add_subparsers(dest="action", required=True, help="Action to perform.")
    subparsers.add_parser("build-images", help="Clone repository and build/publish Docker images.")
    subparsers.add_parser("setup", help="Set up infrastructure (network, bucket, cluster, TPU pool) and deploy LRO DaemonSet.")
    subparsers.add_parser("run", help="Run benchmarks on the GKE cluster.")
    subparsers.add_parser("cleanup", help="Tear down all created infrastructure.")

    # run-all subcommand
    run_all_parser = subparsers.add_parser("run-all", help="Perform setup, build images, run benchmarks, and optionally clean up.")
    run_all_parser.add_argument("--cleanup", action="store_true", help="Tear down infrastructure after running benchmarks.")

    args = parser.parse_args()

    if args.action == "build-images":
        build_images(args)
    elif args.action == "setup":
        setup_infra(args)
    elif args.action == "run":
        run_benchmarks(args)
    elif args.action == "cleanup":
        cleanup(args)
    elif args.action == "run-all":
        # 1. Build images
        build_images(args)
        # 2. Setup infra
        setup_infra(args)
        # 3. Run benchmarks
        run_benchmarks(args)
        # 4. Cleanup if requested
        if args.cleanup:
            cleanup(args)

if __name__ == "__main__":
    main()
