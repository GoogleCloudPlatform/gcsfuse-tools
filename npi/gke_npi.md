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
    make build PROJECT=YOUR_PROJECT_ID GCSFUSE_VERSION=v3.5.6
    ```
This creates images such as `us-docker.pkg.dev/YOUR_PROJECT_ID/gcsfuse-benchmarks/fio-read-benchmark-v3.5.6:latest`.

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

### Sample Pod Specification

Here is a sample `pod.yaml` for running the `read_http1` benchmark on a TPU node:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: fio-bench-read-http1
  namespace: default
  annotations:
    gke-gcsfuse/volumes: "true"
spec:
  nodeSelector:
    cloud.google.com/gke-tpu-topology: 2x2
    cloud.google.com/gke-tpu-accelerator: tpu-v6e-slice
  restartPolicy: Never
  serviceAccountName: benchmark-ksa  # KSA configured with Workload Identity
  containers:
  - name: fio
    image: us-docker.pkg.dev/YOUR_PROJECT_ID/gcsfuse-benchmarks/fio-read-benchmark-v3.5.6:latest
    args: 
    - "--mount-path=/data"
    - "--iterations=5"
    - "--project-id=YOUR_PROJECT_ID"
    - "--bq-dataset-id=YOUR_BQ_DATASET_ID"
    - "--bq-table-id=fio_read_http1"
    volumeMounts:
    - name: data-vol
      mountPath: /data
  volumes:
  - name: data-vol
    csi:
      driver: gcsfuse.csi.storage.gke.io
      volumeAttributes:
        bucketName: "YOUR_BUCKET_NAME"
        mountOptions: "client-protocol=http1"
```

### Running a gRPC Benchmark

To run a gRPC benchmark (e.g., `read_grpc`), update the `args` to emit to the correct table, and critically, update the `mountOptions`:

```yaml
# ... metadata and spec ...
  containers:
  - name: fio
    image: us-docker.pkg.dev/YOUR_PROJECT_ID/gcsfuse-benchmarks/fio-read-benchmark-v3.5.6:latest
    args: 
    - "--mount-path=/data"
    - "--iterations=5"
    - "--project-id=YOUR_PROJECT_ID"
    - "--bq-dataset-id=YOUR_BQ_DATASET_ID"
    - "--bq-table-id=fio_read_grpc"
# ... volumeMounts ...
  volumes:
  - name: data-vol
    csi:
      driver: gcsfuse.csi.storage.gke.io
      volumeAttributes:
        bucketName: "YOUR_BUCKET_NAME"
        mountOptions: "client-protocol=grpc"
```

### Executing the Benchmark

Apply the YAML to your cluster to start the benchmark:

```bash
kubectl apply -f pod.yaml
```

Monitor the logs to ensure the benchmark finishes and metrics are published to BigQuery:

```bash
kubectl logs -f fio-bench-read-http1
```
