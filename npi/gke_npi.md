# Running NPI Benchmarks on GKE

This guide explains how to build and run the NPI (Network Performance Improvement) benchmarks on Google Kubernetes Engine (GKE). Unlike GCE where `npi.py` automatically orchestrates Docker runs, in GKE we manually define and deploy Jobs that use the benchmark images, leveraging the GKE GCS Fuse CSI driver.

> **Note to Operators / Vendors:** Please ensure you have gathered all required variables before executing the scripts. The cluster MUST be prepared with the correct CSI driver and Node Pools.

## Variables Glossary

Before starting, gather the following information. You will replace the placeholders below in the commands and YAML files throughout this guide:
*   `YOUR_PROJECT_ID`: The GCP project ID where your resources (GKE, Artifact Registry, GCS, BigQuery) reside.
*   `YOUR_BUCKET_NAME`: The GCS bucket name to be used for reading/writing test data (e.g., `my-benchmark-bucket` — omit the `gs://` prefix).
*   `YOUR_BQ_DATASET_ID`: The BigQuery dataset where the benchmark results will be uploaded (e.g., `npi_results`).
*   `YOUR_GCSFUSE_VERSION`: The GCSFuse version tag to test (e.g., `v3.5.6`).
*   `YOUR_CLUSTER_NAME`: The name of your GKE cluster.
*   `YOUR_CLUSTER_LOCATION`: The compute zone or region of your GKE cluster (e.g., `us-central1-c` or `us-central1`).

## Step 1: Build the Benchmark Images

The process for building the Docker images is identical to GCE. We use Cloud Build via the provided `Makefile`.

1. Ensure your Artifact Registry repository is created:
    ```bash
    gcloud services enable artifactregistry.googleapis.com --project=YOUR_PROJECT_ID
    gcloud artifacts repositories create gcsfuse-benchmarks \
        --repository-format=docker \
        --location=us \
        --project=YOUR_PROJECT_ID
    ```

2. Build and push the images:
    ```bash
    cd gcsfuse-tools/npi
    make build PROJECT=YOUR_PROJECT_ID GCSFUSE_VERSION=YOUR_GCSFUSE_VERSION

    # Example:
    # make build PROJECT=my-project GCSFUSE_VERSION=v3.5.6
    ```
This creates images such as `us-docker.pkg.dev/YOUR_PROJECT_ID/gcsfuse-benchmarks/fio-read-benchmark-YOUR_GCSFUSE_VERSION:latest`.

> **Verification:** Confirm the images exist in your Artifact Registry before proceeding:
> ```bash
> gcloud artifacts docker images list us-docker.pkg.dev/YOUR_PROJECT_ID/gcsfuse-benchmarks
> ```

## Step 2: GKE Cluster Setup

Before running the benchmarks, you must ensure that your GKE cluster is correctly provisioned for the test. The cluster MUST have the following features enabled:

1. **Workload Identity**: Your cluster must have [Workload Identity enabled](https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity). This is required so that the benchmark pods can securely authenticate to GCP services (GCS, BigQuery) without using exported service account keys.
2. **GCS Fuse CSI Driver**: Ensure that the [Cloud Storage FUSE CSI driver is enabled](https://cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/cloud-storage-fuse-csi-driver) on your cluster. 
3. **Node Pools**: For accurate benchmarking, it's recommended that your cluster has at least two node-pools:
   * A **default node-pool** to run standard Kubernetes system components (kube-dns, etc.).
   * A **dedicated node-pool** corresponding to the specific machine-type you want to test (e.g., specific TPU slices, high-CPU machines, or specific GPU families).
4. **Node Selectors**: You must modify the `nodeSelector` in the provided Job configurations (`gke_pod_specs/*.yaml`) to match the labels of the node-pool where you intend to run the benchmarks.

## Step 3: Connect to the Cluster

Before creating Kubernetes resources like Service Accounts or Jobs, configure `kubectl` to communicate with your GKE cluster:

```bash
gcloud container clusters get-credentials YOUR_CLUSTER_NAME \
    --location=YOUR_CLUSTER_LOCATION \
    --project=YOUR_PROJECT_ID
```

> **Verification:** Confirm that you are connected to the right cluster by running `kubectl config current-context` or `kubectl get nodes`.

## Step 4: Configure Workload Identity (Permissions)

To allow your GKE Jobs to access the GCS bucket and write metrics to BigQuery, you should use **Workload Identity**. This links a Kubernetes Service Account (KSA) to a Google Cloud Service Account (GSA).

1.  **Create a Google Cloud Service Account (GSA):**
    ```bash
    gcloud iam service-accounts create benchmark-gsa \
        --project=YOUR_PROJECT_ID
    ```

2.  **Grant the necessary roles to the GSA:**
    The GSA needs permissions to read/write to the GCS bucket and to insert records into BigQuery.
    ```bash
    # Grant BigQuery Data Editor
    gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
        --member "serviceAccount:benchmark-gsa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
        --role "roles/bigquery.dataEditor"

    # Grant Storage Object User on your bucket
    gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET_NAME \
        --member "serviceAccount:benchmark-gsa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
        --role "roles/storage.objectUser"
    ```

3.  **Create a Kubernetes Service Account (KSA):**
    ```bash
    kubectl create serviceaccount benchmark-ksa \
        --namespace default
    ```

4.  **Bind the KSA to the GSA:**
    ```bash
    gcloud iam service-accounts add-iam-policy-binding benchmark-gsa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
        --role roles/iam.workloadIdentityUser \
        --member "serviceAccount:YOUR_PROJECT_ID.svc.id.goog[default/benchmark-ksa]"
    ```

5.  **Annotate the KSA:**
    ```bash
    kubectl annotate serviceaccount benchmark-ksa \
        --namespace default \
        iam.gke.io/gcp-service-account=benchmark-gsa@YOUR_PROJECT_ID.iam.gserviceaccount.com
    ```

## Step 5: Run the Benchmarks as Jobs

In GKE, we don't use `npi.py`. Instead, we deploy Kubernetes Jobs. The GCSFuse mounting is handled directly by the **GKE GCS Fuse CSI driver**, and we pass the mount path to the benchmark container.

### Important Considerations for GKE

*   **NUMA Binding**: NUMA binding does not currently make sense in GKE. Exclude any NUMA-bound benchmarks (e.g., skip anything with `numa0` or `numa1` in the name).
*   **gRPC Benchmarks**: When running gRPC benchmarks, you **must** change the `mountOptions` in the Job's volume definition to include `"client-protocol=grpc"`.
*   **BigQuery Parameters**: The container needs `args` specifying where to send the metrics since it no longer inherits them from `npi.py`.

### Job Specifications

To make it easier, we have provided a set of ready-to-run YAML Job specifications for common benchmarks in the `gke_pod_specs/` directory:

*   **`gke_pod_specs/fio-read-http1.yaml`**: Standard FIO read test using HTTP/1.1 protocol.
*   **`gke_pod_specs/fio-write-grpc.yaml`**: Standard FIO write test using gRPC protocol. Note that `mountOptions` is set to `"client-protocol=grpc"`.
*   **`gke_pod_specs/orbax-read-grpc.yaml`**: Orbax-emulated read test using gRPC protocol.

Before applying these, you must edit the file you wish to run and replace the placeholders (`YOUR_PROJECT_ID`, `YOUR_GCSFUSE_VERSION`, `YOUR_BQ_DATASET_ID`, `YOUR_BUCKET_NAME`) with your actual values.

### Executing Individual Benchmarks

Apply your modified YAML to your cluster to start a single benchmark:

```bash
kubectl apply -f gke_pod_specs/fio-read-http1.yaml
```

Monitor the logs to ensure the benchmark finishes and metrics are published to BigQuery:

```bash
kubectl logs -f job/fio-bench-read-http1
```

### Executing All Benchmarks Sequentially

To avoid resource contention and interference, it is recommended to run the benchmarks one at a time. We have provided a helper script that iterates through all YAML files in the `gke_pod_specs/` directory, waits for each to finish, and cleans up the Job before moving to the next.

```bash
# Ensure the script is executable
chmod +x run_gke_benchmarks.sh

# Run all templates sequentially
./run_gke_benchmarks.sh
```

> **Troubleshooting Tip:** If a Job fails or gets stuck in `ContainerCreating` or `Pending`, use `kubectl describe pod -l job-name=<job_name>` to view Kubernetes events. A common issue is a mismatched `nodeSelector` or insufficient IAM permissions.

## Step 6: Analyze Results

After your benchmarks have completed successfully, the FIO JSON output metrics will be populated in your BigQuery tables. 

To learn how to extract useful performance characteristics such as throughput (MiB/s) and latency (ms) from the raw FIO JSON in BigQuery, refer to the [BigQuery Performance Analysis Queries](bq_queries.md) guide.
