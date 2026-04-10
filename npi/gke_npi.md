# Running NPI Benchmarks on GKE

This guide explains how to build and run the NPI (Network Performance Improvement) benchmarks on Google Kubernetes Engine (GKE). Unlike GCE where `npi.py` automatically orchestrates Docker runs, in GKE we manually define and deploy Pods that use the benchmark images, leveraging the GKE GCS Fuse CSI driver.

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

## Step 2: Configure Workload Identity (Permissions)

To allow your GKE Pods to access the GCS bucket and write metrics to BigQuery, you should use **Workload Identity**. This links a Kubernetes Service Account (KSA) to a Google Cloud Service Account (GSA).

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

## Step 3: Run the Benchmarks as Pods

In GKE, we don't use `npi.py`. Instead, we deploy Kubernetes Pods. The GCSFuse mounting is handled directly by the **GKE GCS Fuse CSI driver**, and we pass the mount path to the benchmark container.

### Important Considerations for GKE

*   **NUMA Binding**: NUMA binding does not currently make sense in GKE. Exclude any NUMA-bound benchmarks (e.g., skip anything with `numa0` or `numa1` in the name).
*   **gRPC Benchmarks**: When running gRPC benchmarks, you **must** change the `mountOptions` in the Pod's volume definition to include `"client-protocol=grpc"`.
*   **BigQuery Parameters**: The container needs `args` specifying where to send the metrics since it no longer inherits them from `npi.py`.

### Pod Specifications

To make it easier, we have provided a set of ready-to-run YAML pod specifications for common benchmarks in the `gke_pod_specs/` directory:

*   **`gke_pod_specs/fio-read-http1.yaml`**: Standard FIO read test using HTTP/1.1 protocol.
*   **`gke_pod_specs/fio-write-grpc.yaml`**: Standard FIO write test using gRPC protocol. Note that `mountOptions` is set to `"client-protocol=grpc"`.
*   **`gke_pod_specs/orbax-read-grpc.yaml`**: Orbax-emulated read test using gRPC protocol.

Before applying these, you must edit the file you wish to run and replace the placeholders (`YOUR_PROJECT_ID`, `YOUR_GCSFUSE_VERSION`, `YOUR_BQ_DATASET_ID`, `YOUR_BUCKET_NAME`) with your actual values.

### Executing the Benchmark

Apply your modified YAML to your cluster to start the benchmark:

```bash
kubectl apply -f gke_pod_specs/fio-read-http1.yaml
```

Monitor the logs to ensure the benchmark finishes and metrics are published to BigQuery:

```bash
kubectl logs -f fio-bench-read-http1
```
