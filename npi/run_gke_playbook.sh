#!/bin/bash
set -e

usage() {
    echo "Usage: $0 -p <PROJECT_ID> -v <GCSFUSE_VERSION> -b <BUCKET_NAME> -d <BQ_DATASET_ID> -c <CLUSTER_NAME> -l <CLUSTER_LOCATION>"
    exit 1
}

while getopts "p:v:b:d:c:l:" opt; do
    case "$opt" in
        p) PROJECT_ID="$OPTARG" ;;
        v) GCSFUSE_VERSION="$OPTARG" ;;
        b) BUCKET_NAME="$OPTARG" ;;
        d) BQ_DATASET_ID="$OPTARG" ;;
        c) CLUSTER_NAME="$OPTARG" ;;
        l) CLUSTER_LOCATION="$OPTARG" ;;
        *) usage ;;
    esac
done

if [ -z "$PROJECT_ID" ] || [ -z "$GCSFUSE_VERSION" ] || [ -z "$BUCKET_NAME" ] || [ -z "$BQ_DATASET_ID" ] || [ -z "$CLUSTER_NAME" ] || [ -z "$CLUSTER_LOCATION" ]; then
    echo "Error: Missing required arguments."
    usage
fi

echo "=== Step 1: Environment Setup & Connect to Cluster ==="
gcloud config set project "$PROJECT_ID"
gcloud container clusters get-credentials "$CLUSTER_NAME" --location="$CLUSTER_LOCATION" --project="$PROJECT_ID"
kubectl get nodes

echo "=== Step 2: Build Benchmark Images ==="
gcloud services enable artifactregistry.googleapis.com --project="$PROJECT_ID" || true
gcloud artifacts repositories create gcsfuse-benchmarks --repository-format=docker --location=us --project="$PROJECT_ID" || true
make build PROJECT="$PROJECT_ID" GCSFUSE_VERSION="$GCSFUSE_VERSION"
gcloud artifacts docker images list "us-docker.pkg.dev/$PROJECT_ID/gcsfuse-benchmarks"

echo "=== Step 3: Configure Workload Identity ==="
gcloud iam service-accounts create benchmark-gsa --project="$PROJECT_ID" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:benchmark-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/bigquery.dataEditor"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
    --member="serviceAccount:benchmark-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/storage.objectUser"

kubectl create serviceaccount benchmark-ksa --namespace default || true
gcloud iam service-accounts add-iam-policy-binding "benchmark-gsa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role roles/iam.workloadIdentityUser \
    --member "serviceAccount:${PROJECT_ID}.svc.id.goog[default/benchmark-ksa]"

kubectl annotate serviceaccount benchmark-ksa \
    --namespace default \
    "iam.gke.io/gcp-service-account=benchmark-gsa@${PROJECT_ID}.iam.gserviceaccount.com" --overwrite

echo "=== Step 4: Run Benchmarks Sequentially ==="
mkdir -p tmp_gke_specs
for file in gke_pod_specs/*.yaml; do
    filename=$(basename "$file")
    sed -e "s/YOUR_PROJECT_ID/$PROJECT_ID/g" \
        -e "s/YOUR_GCSFUSE_VERSION/$GCSFUSE_VERSION/g" \
        -e "s/YOUR_BQ_DATASET_ID/$BQ_DATASET_ID/g" \
        -e "s/YOUR_BUCKET_NAME/$BUCKET_NAME/g" \
        "$file" > "tmp_gke_specs/$filename"
done

sed 's/gke_pod_specs/tmp_gke_specs/g' run_gke_benchmarks.sh > tmp_run_benchmarks.sh
chmod +x tmp_run_benchmarks.sh
./tmp_run_benchmarks.sh

echo "=== Step 5: Clean Up ==="
rm -rf tmp_gke_specs tmp_run_benchmarks.sh
echo "GKE Benchmarks Complete!"
