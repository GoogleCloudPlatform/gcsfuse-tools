# GCS DirectPath & MSS Verification Suite (`npi_setup_verify.py`)

`npi_setup_verify.py` is an automation script designed to verify Google Cloud Storage (GCS) **DirectPath** availability and log the negotiated **MSS (Max Segment Size)** on GKE clusters using a minimal Go SDK gRPC workload.

## Key Features
- **Dual-Stack (IPv4/IPv6) Orchestration**: Configures VPC networks, subnetworks, and GKE Datapath V2 clusters with IPv4 and IPv6 stack support to enable DirectPath connection paths.
- **DirectPath Enforcement**: Verifies DirectPath using the Go Storage client's `experimental.WithDirectConnectivityEnforced()` option, failing fast if a DirectPath path cannot be established.
- **Connection Analysis**: Captures active TCP socket connections to ensure they terminate on DirectPath IPv6 ranges (`2001:4860:8040:7b::/64` on port `14002`) and prints negotiated MSS sizes.
- **Flexible Deployments**: Supports deploying workloads to standard VM node pools (`--no-tpu`) or specific TPU accelerator node pools.

---

## Usage

```sh
python3 npi_setup_verify.py [ACTION] [OPTIONS]
```

### Available Actions
- `setup-global`: Sets up the dual-stack VPC network, subnetwork, GCS bucket, and Workload Identity IAM permissions.
- `build-images`: Builds and pushes the validation Docker images to Artifact Registry using Cloud Build.
- `run-verify`: Creates a GKE cluster (v1.34 or v1.35), creates the node pools, runs the validation workload, outputs logs, and tears down the cluster.
- `cleanup`: Complete teardown of GKE clusters, buckets, and VPC network.

### Command-line Options
- `--project-id`: (Required) GCP Project ID.
- `--cluster-name`: Name of the GKE cluster. Defaults to `grpc-verify-cluster`.
- `--no-tpu`: Runs workload on standard GKE nodes instead of provisioning a TPU node pool.
- `--gke-version`: GKE Master/Node version. Defaults to `1.35.3-gke.2190000`.
- `--region`: GCP Region. Defaults to `europe-west4`.
- `--zone`: GCP Zone. Defaults to `europe-west4-a`.
- `--reservation-affinity`: Reservation affinity for the TPU node pool (`specific`, `any`, `none`).
- `--reservation`: Name of the specific GCE reservation to use (required if reservation-affinity is `specific`).
- `--keep-cluster`: Keep GKE cluster alive after validation completes (does not auto-delete).

---

## Examples

### 1. Run Verification on standard nodes (non-TPU)
```sh
python3 npi_setup_verify.py run-verify \
    --project-id=my-gcp-project \
    --no-tpu
```

### 2. Run Verification on TPU nodes with a specific reservation
```sh
python3 npi_setup_verify.py run-verify \
    --project-id=my-gcp-project \
    --reservation-affinity=specific \
    --reservation=my-tpu-reservation
```

### 3. Verification Job Output Excerpt (Success)
When DirectPath is active, the job logs will show:
```
=== Connection Analysis ===
Total active/established connections: 116
- Conn #1: Address=[2001:4860:8040:7b:0:113:3000:8c04]:14002, IPv6=true, DirectPathRange=true, MSS=1388
- Conn #2: Address=[2001:4860:8040:7b:0:113:16d6:2c36]:14002, IPv6=true, DirectPathRange=true, MSS=1388
...
RESULT: DirectPath is ACTIVE!
```
