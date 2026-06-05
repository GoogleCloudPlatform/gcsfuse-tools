# Automated E2E Regression Testing Suite (`npi_setup.py`)

`npi_setup.py` is an end-to-end automation driver designed to provision GKE clusters, build Docker benchmark images, execute baseline vs. regression comparison tests, and query/output the relative throughput changes.

## Key Features
- **Concurrent Cluster Provisioning**: Creates both GKE cluster control planes (default node pools) in parallel, saving 5-7 minutes.
- **Concurrent Cloud Build Pipeline**: Submits and builds all multi-arch benchmark images on Cloud Build concurrently with cluster creation.
- **Sequential TPU Resource Management**: Manages node pool provisioning sequentially to stay within limited TPU v6e quota/reservations.
- **Node-level LRO Toggling**: Automatically deploys helper DaemonSets to enable and disable Large Receive Offload (LRO) dynamically on node interfaces.
- **Late-Exit Phase Failure Summary Banners**: If a benchmark run fails, it cleans up all resources first, and then outputs a warning banner with failure logs to prevent log-scroll loss.
- **Workspace Redirection**: Clones repository data to `/tmp` instead of polluting the local user workspace.

## Usage

```sh
python3 npi_setup.py [ACTION] [OPTIONS]
```

### Available Actions
- `setup-global`: Sets up the VPC Network, Subnet, GCS Bucket, and configures Workload Identity IAM roles.
- `build-images`: Clones the GCSFuse repo and triggers concurrent Cloud Build multi-platform image compilation.
- `run-all`: Full automated run. Performs global infra setup, concurrent builds, GKE cluster provisioning, baseline vs. regression runs (with LRO ON and LRO OFF), and outputs comparison results.
- `cleanup`: Tears down GKE clusters concurrently, deletes the bucket, subnet, and VPC.
- `compare`: Queries BigQuery directly to compare the throughput change between GKE baseline and regression results.

### Options

*   `--project-id`: (Required) GCP Project ID.
*   `--cluster-name`: Name of the GKE cluster prefix. Defaults to `npi-benchmark-cluster`.
*   `--bucket-name`: GCS Bucket name. Defaults to `npi-benchmark-bucket-<project-id>-<region>`.
*   `--network-name`: VPC Network name. Defaults to `npi-benchmark-net`.
*   `--subnet-name`: Subnet name. Defaults to `npi-benchmark-subnet`.
*   `--region`: GCP Region. Defaults to `europe-west4`.
*   `--zone`: GCP Zone. Defaults to `europe-west4-a`.
*   `--gke-version`: Regression GKE version. Defaults to `1.35.3-gke.2190000`.
*   `--baseline-gke-version`: Baseline GKE version. Defaults to `1.33.11-gke.1197000`.
*   `--tpu-machine-type`: TPU machine type for node pools. Defaults to `ct6e-standard-4t`.
*   `--reservation-affinity`: GCE Reservation affinity for the TPU node pool (`specific`, `any`, `none`).
*   `--reservation`: Name of the specific GCE reservation to use (required if reservation affinity is `specific`).
*   `--keep-clusters`: Keep clusters alive after execution (disables automatic GKE cluster deletion).

## Example: Run Full Comparison Suite with a TPU Reservation

```sh
python3 npi_setup.py run-all \
    --project-id=my-gcp-project \
    --reservation-affinity=specific \
    --reservation=my-tpu-reservation
```
