#!/usr/bin/env python3
"""Automation script for setting up and tearing down GKE clusters and bucket configs for NPI benchmarks."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

NET_CONFIG_DAEMONSET_YAML = """
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: configure-net
  namespace: kube-system
  labels:
    app: configure-net
spec:
  selector:
    matchLabels:
      app: configure-net
  template:
    metadata:
      labels:
        app: configure-net
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
          echo "=== CONFIGURE NET START ==="
          echo "Setting LRO to {lro_value} on eth0"
          nsenter --net=/host-proc/1/ns/net ethtool -K eth0 lro {lro_value} || true
          echo "=== RING PARAMETERS eth0 ==="
          nsenter --net=/host-proc/1/ns/net ethtool -g eth0 || true
          echo "=== END RING PARAMETERS ==="
          {tcp_data_split_cmd}
          echo "=== OFFLOADS eth0 ==="
          nsenter --net=/host-proc/1/ns/net ethtool -k eth0 | grep large-receive-offload || true
          echo "=== CONFIGURE NET END ==="
          tail -f /dev/null
      volumes:
      - name: host-proc
        hostPath:
          path: /proc
"""

REPO_DIR = os.path.join(tempfile.gettempdir(), "gcsfuse-tools-npi-repo")


def run_cmd(cmd, check=True):
  print(f"\n[INFO] Running: {' '.join(cmd)}")
  return subprocess.run(cmd, check=check, text=True)


def clone_repo():
  if os.path.exists(REPO_DIR):
    print(f"\n[INFO] Removing existing repo directory: {REPO_DIR}")
    shutil.rmtree(REPO_DIR)
  run_cmd([
      "git",
      "clone",
      "https://github.com/GoogleCloudPlatform/gcsfuse-tools.git",
      REPO_DIR,
  ])


def build_images(project_id, gcsfuse_version):
  print("\n[INFO] Creating Artifact Registry repository...")
  run_cmd(
      [
          "gcloud",
          "artifacts",
          "repositories",
          "create",
          "gcsfuse-benchmarks",
          "--repository-format=docker",
          "--location=us",
          f"--project={project_id}",
      ],
      check=False,
  )

  cwd = os.path.join(REPO_DIR, "npi")
  print(f"\n[INFO] Building and pushing images in {cwd}...")
  subprocess.run(
      ["make", f"PROJECT={project_id}", f"GCSFUSE_VERSION={gcsfuse_version}"],
      check=True,
      text=True,
      cwd=cwd,
  )


def setup_global_infra(args):
  project_id = args.project_id
  region = args.region
  network_name = args.network_name
  subnet_name = args.subnet_name
  bucket_name = (
      args.bucket_name or f"npi-benchmark-bucket-{project_id}-{region}"
  )

  ksa_name = "gcsfuse-npi-ksa"
  namespace = "default"

  # 1. Enable APIs
  run_cmd([
      "gcloud",
      "services",
      "enable",
      "artifactregistry.googleapis.com",
      "container.googleapis.com",
      f"--project={project_id}",
  ])

  # 2. Create VPC Network
  run_cmd(
      [
          "gcloud",
          "compute",
          "networks",
          "create",
          network_name,
          f"--project={project_id}",
          "--subnet-mode=custom",
          f"--mtu={args.mtu}",
      ],
      check=False,
  )

  # 3. Create Subnet
  run_cmd(
      [
          "gcloud",
          "compute",
          "networks",
          "subnets",
          "create",
          subnet_name,
          f"--project={project_id}",
          f"--network={network_name}",
          f"--region={region}",
          "--range=10.0.0.0/20",
          "--enable-private-ip-google-access",
      ],
      check=False,
  )

  # 4. Create GCS Bucket
  run_cmd(
      [
          "gcloud",
          "storage",
          "buckets",
          "create",
          f"gs://{bucket_name}",
          f"--project={project_id}",
          f"--location={region}",
          "--uniform-bucket-level-access",
          "--enable-hierarchical-namespace",
      ],
      check=False,
  )

  # 5. Fetch Project Number
  res = subprocess.run(
      [
          "gcloud",
          "projects",
          "describe",
          project_id,
          "--format=value(projectNumber)",
      ],
      capture_output=True,
      text=True,
      check=True,
  )
  project_number = res.stdout.strip()

  # 6. Bind KSA Principal to GCS and BQ
  principal = f"principal://iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/{project_id}.svc.id.goog/subject/ns/{namespace}/sa/{ksa_name}"

  # GCS Bucket binding
  run_cmd([
      "gcloud",
      "storage",
      "buckets",
      "add-iam-policy-binding",
      f"gs://{bucket_name}",
      f"--member={principal}",
      "--role=roles/storage.objectAdmin",
  ])

  # BigQuery bindings (Project level)
  for role in ["roles/bigquery.dataEditor", "roles/bigquery.jobUser"]:
    run_cmd([
        "gcloud",
        "projects",
        "add-iam-policy-binding",
        project_id,
        f"--member={principal}",
        f"--role={role}",
        "--condition=None",
        "--quiet",
    ])


def get_node_pool_status(project_id, zone, cluster_name, pool_name):
  cmd = [
      "gcloud",
      "container",
      "node-pools",
      "describe",
      pool_name,
      f"--cluster={cluster_name}",
      f"--zone={zone}",
      f"--project={project_id}",
      "--format=value(status)",
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
      print(
          "[ERROR] TPU node pool provisioning timed out after"
          f" {args.tpu_provision_timeout_hours} hours.",
          file=sys.stderr,
      )
      sys.exit(1)

    print(f"\n[INFO] [Attempt {retry_count + 1}] Creating TPU Node Pool...")

    create_cmd = [
        "gcloud",
        "container",
        "node-pools",
        "create",
        "tpu-pool",
        f"--cluster={cluster_name}",
        f"--project={project_id}",
        f"--zone={zone}",
        f"--node-locations={zone}",
        f"--machine-type={tpu_machine}",
        "--num-nodes=1",
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
        "gcloud",
        "container",
        "node-pools",
        "delete",
        "tpu-pool",
        f"--cluster={cluster_name}",
        f"--zone={zone}",
        f"--project={project_id}",
        "--quiet",
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
  res = subprocess.run(
      [
          "gcloud",
          "container",
          "clusters",
          "describe",
          cluster_name,
          f"--zone={zone}",
          f"--project={project_id}",
      ],
      capture_output=True,
  )
  if res.returncode == 0:
    print(
        f"\n[INFO] Cluster {cluster_name} already exists. Deleting it to ensure"
        " clean slate..."
    )
    delete_cluster(args, cluster_name)

  print(
      f"\n[INFO] Creating GKE Cluster (Control Plane): {cluster_name} (Version:"
      f" {gke_version})..."
  )
  run_cmd([
      "gcloud",
      "container",
      "clusters",
      "create",
      cluster_name,
      f"--project={project_id}",
      f"--zone={zone}",
      f"--cluster-version={gke_version}",
      f"--network={network_name}",
      f"--subnetwork={subnet_name}",
      "--machine-type=n2-standard-8",
      "--num-nodes=1",
      f"--workload-pool={project_id}.svc.id.goog",
  ])


def provision_tpu_and_setup_ksa(args, cluster_name):
  project_id = args.project_id
  zone = args.zone
  ksa_name = "gcsfuse-npi-ksa"
  namespace = "default"

  # Add TPU Node Pool (with retries and reservation support)
  create_tpu_node_pool_with_retries(args, cluster_name)

  # Get Cluster Credentials
  run_cmd([
      "gcloud",
      "container",
      "clusters",
      "get-credentials",
      cluster_name,
      f"--zone={zone}",
      f"--project={project_id}",
  ])

  # Create KSA
  run_cmd(
      [
          "kubectl",
          "create",
          "serviceaccount",
          ksa_name,
          f"--namespace={namespace}",
      ],
      check=False,
  )


def delete_node_pool(args, cluster_name, pool_name):
  print(
      f"\n[INFO] Deleting node pool {pool_name} from cluster {cluster_name}..."
  )
  run_cmd(
      [
          "gcloud",
          "container",
          "node-pools",
          "delete",
          pool_name,
          f"--cluster={cluster_name}",
          f"--zone={args.zone}",
          f"--project={args.project_id}",
          "--quiet",
      ],
      check=False,
  )


def configure_network_settings(args, cluster_name, lro_status, tcp_data_split_status=None):
  # Delete any existing daemonset first to ensure clean state and logs
  subprocess.run(
      [
          "kubectl",
          "delete",
          "daemonset",
          "configure-net",
          "-n=kube-system",
          "--ignore-not-found=true",
      ],
      check=False,
  )

  tcp_data_split_cmd = ""
  if tcp_data_split_status == "off":
    tcp_data_split_cmd = """
          echo "Setting tcp-data-split to off on eth0"
          nsenter --net=/host-proc/1/ns/net ethtool -G eth0 tcp-data-split off || true
          echo "=== RING PARAMETERS eth0 AFTER CHANGE ==="
          nsenter --net=/host-proc/1/ns/net ethtool -g eth0 || true
          echo "=== END RING PARAMETERS AFTER CHANGE ==="
    """

  print(
      f"\n[INFO] Configuring GKE nodes on {cluster_name}: LRO={lro_status},"
      f" tcp-data-split={tcp_data_split_status}..."
  )

  daemonset_rendered = NET_CONFIG_DAEMONSET_YAML.format(
      lro_value=lro_status, tcp_data_split_cmd=tcp_data_split_cmd
  )
  proc = subprocess.Popen(
      ["kubectl", "apply", "-f", "-"], stdin=subprocess.PIPE, text=True
  )
  proc.communicate(input=daemonset_rendered)
  if proc.returncode != 0:
    print(
        f"[ERROR] Failed to deploy network config DaemonSet on {cluster_name}",
        file=sys.stderr,
    )
    sys.exit(1)

  print("[INFO] Waiting for network configuration pods to roll out...")
  subprocess.run(
      [
          "kubectl",
          "rollout",
          "status",
          "daemonset/configure-net",
          "--namespace=kube-system",
          "--timeout=2m",
      ],
      check=True,
  )
  time.sleep(10)


def get_and_check_tcp_data_split(namespace="kube-system", label_selector="app=configure-net"):
  # Get pods JSON
  cmd = [
      "kubectl",
      "get",
      "pods",
      f"-n={namespace}",
      f"-l={label_selector}",
      "-o=json",
  ]
  res = subprocess.run(cmd, capture_output=True, text=True, check=True)
  pods_data = json.loads(res.stdout)
  
  tcp_data_split_on = False
  for item in pods_data.get("items", []):
    pod_name = item["metadata"]["name"]
    log_cmd = [
        "kubectl",
        "logs",
        pod_name,
        f"-n={namespace}",
    ]
    log_res = subprocess.run(log_cmd, capture_output=True, text=True, check=True)
    log_content = log_res.stdout
    
    print(f"\n--- Logs for Pod {pod_name} ---")
    print(log_content)
    print("--------------------------------")
    
    for line in log_content.splitlines():
      if "tcp data split" in line.lower().replace("-", " ") and "on" in line.lower():
        tcp_data_split_on = True
        
  return tcp_data_split_on


def run_benchmarks_for_cluster(args, cluster_name, dataset_id):
  ksa_name = "gcsfuse-npi-ksa"
  bucket_name = (
      args.bucket_name
      or f"npi-benchmark-bucket-{args.project_id}-{args.region}"
  )

  cmd = [
      "python3",
      "npi_gke.py",
      f"--bucket-name={bucket_name}",
      f"--project-id={args.project_id}",
      f"--bq-dataset-id={dataset_id}",
      f"--kubernetes-service-account={ksa_name}",
      f"--cluster-name={cluster_name}",
      f"--location={args.zone}",
      "--node-selector=cloud.google.com/gke-tpu-accelerator=tpu-v6e-slice,cloud.google.com/gke-tpu-topology=2x2",
      "--resources-limits=google.com/tpu=4",
      "-b",
      "go_read_http1",
      "go_read_grpc",
  ]
  cwd = os.path.join(REPO_DIR, "npi")
  print(f"\n[INFO] Executing benchmarks for {cluster_name} in {cwd}...")
  subprocess.run(cmd, check=True, text=True, cwd=cwd)


def delete_cluster(args, cluster_name):
  run_cmd(
      [
          "gcloud",
          "container",
          "clusters",
          "delete",
          cluster_name,
          f"--zone={args.zone}",
          f"--project={args.project_id}",
          "--quiet",
      ],
      check=False,
  )


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
    print(
        f"[WARNING] Failed to query BQ table {dataset_id}.{table_id}: {e}",
        file=sys.stderr,
    )
  return 0.0


def compare_results(args, bq_dataset_id, ran_tcp_data_split_off=None):
  project_id = args.project_id

  dataset_reg_on = f"{bq_dataset_id}_regression_lro_on"
  dataset_reg_off = f"{bq_dataset_id}_regression_lro_on_tcp_data_split_off"

  bw_reg_on = get_average_bandwidth(
      project_id, dataset_reg_on, "go_client_read_grpc"
  )

  print("\n==================================================")
  print("=== PERFORMANCE COMPARISON (Go Client gRPC) ===")
  print("==================================================")
  print(f"Regression (GKE {args.gke_version}) LRO ON:  {bw_reg_on:.2f} MiB/s")
  
  bw_reg_off = get_average_bandwidth(
      project_id, dataset_reg_off, "go_client_read_grpc"
  )
  
  if ran_tcp_data_split_off or (ran_tcp_data_split_off is None and bw_reg_off > 0):
    print(f"Regression (GKE {args.gke_version}) LRO ON & TCP Data Split OFF: {bw_reg_off:.2f} MiB/s")
    print("--------------------------------------------------")
    if bw_reg_on > 0 and bw_reg_off > 0:
      change = ((bw_reg_off - bw_reg_on) / bw_reg_on) * 100.0
      print(
          "Throughput Change (LRO ON) TCP Data Split Default vs OFF:"
          f" {change:+.2f}%"
      )
  else:
    print("--------------------------------------------------")
    print("[INFO] TCP Data Split OFF phase was not run or has no data.")


def run_all(args):
  # Determine cluster name (only regression cluster)
  regression_cluster = f"{args.cluster_name}-regression"

  bq_dataset_id = args.bq_dataset_id

  # 1. Setup global infrastructure (VPC, GCS Bucket, direct WI bindings)
  setup_global_infra(args)

  errors = {}

  def run_image_build():
    try:
      clone_repo()
      build_images(args.project_id, args.gcsfuse_version)
    except Exception as e:
      errors["image_build"] = e
      print(f"[ERROR] Image building failed: {e}", file=sys.stderr)

  def run_setup_regression():
    try:
      setup_cluster_control_plane(args, regression_cluster, args.gke_version)
    except Exception as e:
      errors["regression_setup"] = e
      print(
          f"[ERROR] Regression GKE control plane setup failed: {e}",
          file=sys.stderr,
      )

  # 2. Run image builds and GKE cluster creation concurrently
  print(
      "\n[INFO] Starting concurrent image building and cluster control plane"
      " provisioning..."
  )
  t_build = threading.Thread(target=run_image_build)
  t_regression = threading.Thread(target=run_setup_regression)

  t_build.start()
  t_regression.start()

  t_build.join()
  t_regression.join()

  # Check if any errors occurred during concurrent initialization phase
  if errors:
    print("\n" + "=" * 60)
    print("===           CONCURRENT INITIALIZATION FAILURE              ===")
    print("=" * 60)
    for name, err in errors.items():
      print(f"[FAIL] Task '{name}' failed with error: {err}")
    print("=" * 60 + "\n")
    # Clean up any created GKE clusters
    if not args.keep_clusters:
      print("[INFO] Cleaning up GKE cluster after initialization failure...")
      delete_cluster(args, regression_cluster)
    sys.exit(1)

  print(
      "\n[INFO] Concurrent initialization complete. Proceeding with sequential"
      " TPU benchmark phases..."
  )

  # 3. Regression Phase
  print(f"\n==================================================")
  print(f"=== Regression GKE {args.gke_version} ===")
  print(f"==================================================")
  regression_error = None
  ran_tcp_data_split_off = False
  try:
    provision_tpu_and_setup_ksa(args, regression_cluster)

    # 3a. Run Regression with LRO ON
    configure_network_settings(args, regression_cluster, "on")
    
    # Check if TCP data split is on
    tcp_data_split_on = get_and_check_tcp_data_split()
    print(f"[INFO] Detected TCP data split status: {'ON' if tcp_data_split_on else 'OFF'}")
    
    # Run the first benchmark (LRO ON, TCP data split default)
    run_benchmarks_for_cluster(
        args, regression_cluster, f"{bq_dataset_id}_regression_lro_on"
    )

    # 3b. If TCP data split is on, disable it and run again
    if tcp_data_split_on:
      print("\n[INFO] TCP data split is ON. Disabling it and running benchmark again...")
      configure_network_settings(args, regression_cluster, "on", tcp_data_split_status="off")
      
      # Log settings again to verify it is off
      _ = get_and_check_tcp_data_split()
      
      run_benchmarks_for_cluster(
          args, regression_cluster, f"{bq_dataset_id}_regression_lro_on_tcp_data_split_off"
      )
      ran_tcp_data_split_off = True
    else:
      print("\n[INFO] TCP data split is already OFF. Skipping TCP data split off phase.")

  except Exception as e:
    regression_error = e
  finally:
    # Delete only the TPU node pool
    delete_node_pool(args, regression_cluster, "tpu-pool")

  if regression_error:
    print("\n" + "=" * 60)
    print("===                        FAILURE                           ===")
    print("=" * 60)
    print(f"[FAIL] Regression GKE phase failed with error: {regression_error}")
    print(
        "Please check the console logs above for the specific benchmark failure"
        " details."
    )
    print("=" * 60 + "\n")
    if not args.keep_clusters:
      print("[INFO] Cleaning up GKE cluster after failure...")
      delete_cluster(args, regression_cluster)
    raise regression_error

  # 4. Teardown GKE Cluster
  if not args.keep_clusters:
    print("\n[INFO] Deleting GKE cluster...")
    delete_cluster(args, regression_cluster)

  # 5. Comparison Phase
  compare_results(args, bq_dataset_id, ran_tcp_data_split_off=ran_tcp_data_split_off)


def cleanup(args):
  project_id = args.project_id
  region = args.region
  network_name = args.network_name
  subnet_name = args.subnet_name
  bucket_name = (
      args.bucket_name or f"npi-benchmark-bucket-{project_id}-{region}"
  )

  regression_cluster = f"{args.cluster_name}-regression"

  # 1. Delete regression cluster
  print(f"\n[INFO] Deleting GKE cluster {regression_cluster}...")
  delete_cluster(args, regression_cluster)

  # 2. Delete GCS Bucket
  run_cmd(
      [
          "gcloud",
          "storage",
          "rm",
          "-r",
          f"gs://{bucket_name}",
          f"--project={project_id}",
          "--quiet",
      ],
      check=False,
  )

  # 3. Delete Subnet & VPC Network
  run_cmd(
      [
          "gcloud",
          "compute",
          "networks",
          "subnets",
          "delete",
          subnet_name,
          f"--region={region}",
          f"--project={project_id}",
          "--quiet",
      ],
      check=False,
  )
  run_cmd(
      [
          "gcloud",
          "compute",
          "networks",
          "delete",
          network_name,
          f"--project={project_id}",
          "--quiet",
      ],
      check=False,
  )

  print("\n--- Cleanup completed! ---")


def main():
  parser = argparse.ArgumentParser(
      description="NPI Benchmark GKE Cluster setup/cleanup automation."
  )
  parser.add_argument("--project-id", required=True, help="GCP Project ID.")
  parser.add_argument(
      "--cluster-name",
      default="npi-benchmark-cluster",
      help="Name of GKE cluster.",
  )
  parser.add_argument(
      "--bucket-name",
      default=None,
      help=(
          "Name of GCS Bucket (default:"
          " npi-benchmark-bucket-<project-id>-<region>)."
      ),
  )
  parser.add_argument(
      "--network-name", default="npi-benchmark-net", help="VPC Network name."
  )
  parser.add_argument(
      "--subnet-name", default="npi-benchmark-subnet", help="Subnet name."
  )
  parser.add_argument("--region", default="europe-west4", help="GCP region.")
  parser.add_argument("--zone", default="europe-west4-a", help="GCP zone.")
  parser.add_argument(
      "--gke-version",
      default="1.35.3-gke.2190000",
      help="GKE version with regression.",
  )
  parser.add_argument(
      "--baseline-gke-version",
      default="1.33.11-gke.1197000",
      help="GKE baseline version.",
  )
  parser.add_argument(
      "--tpu-machine-type",
      default="ct6e-standard-4t",
      help="TPU machine type for node pool.",
  )
  parser.add_argument(
      "--bq-dataset-id",
      default="npi_benchmarks",
      help="BigQuery Dataset ID for storing results.",
  )
  parser.add_argument(
      "--gcsfuse-version",
      default="master",
      help="GCSFuse branch/version to build images for.",
  )
  parser.add_argument(
      "--keep-clusters",
      action="store_true",
      help="Keep clusters alive after run (do not delete them sequentially).",
  )
  parser.add_argument("--mtu", type=int, default=8896, help="VPC Network MTU.")
  parser.add_argument(
      "--tpu-provision-timeout-hours",
      type=float,
      default=2.0,
      help="TPU node pool provisioning timeout in hours. Default: 2.0.",
  )
  parser.add_argument(
      "--reservation-affinity",
      default=None,
      choices=["any", "none", "specific"],
      help="GCE Reservation affinity for TPU node pool.",
  )
  parser.add_argument(
      "--reservation",
      default=None,
      help=(
          "Name of GCE reservation to use (required if reservation-affinity is"
          " specific)."
      ),
  )

  subparsers = parser.add_subparsers(
      dest="action", required=True, help="Action to perform."
  )
  subparsers.add_parser(
      "setup-global", help="Setup VPC Network, Bucket, and IAM permissions."
  )
  subparsers.add_parser(
      "build-images",
      help="Clone gcsfuse-tools and build/push benchmark images.",
  )
  subparsers.add_parser(
      "run-all",
      help=(
          "Perform setup, build-images, benchmark runs for baseline and"
          " regression GKE versions, and compare throughput."
      ),
  )
  subparsers.add_parser(
      "cleanup", help="Tear down GKE clusters, bucket, and VPC network."
  )
  subparsers.add_parser(
      "compare",
      help="Compare BigQuery results of baseline vs regression directly.",
  )

  args = parser.parse_args()

  if args.reservation_affinity == "specific" and not args.reservation:
    parser.error(
        "--reservation is required when --reservation-affinity is set to"
        " specific."
    )

  if args.action == "setup-global":
    setup_global_infra(args)
  elif args.action == "build-images":
    clone_repo()
    build_images(args.project_id, args.gcsfuse_version)
  elif args.action == "run-all":
    run_all(args)
  elif args.action == "cleanup":
    cleanup(args)
  elif args.action == "compare":
    compare_results(args, args.bq_dataset_id)


if __name__ == "__main__":
  main()