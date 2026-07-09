#!/bin/bash
set -e

usage() {
    echo "Usage: $0 -p <PROJECT_ID> -v <GCSFUSE_VERSION> -b <BUCKET_NAME> -d <BQ_DATASET_ID> -n <VM_NAME> -z <VM_ZONE>"
    exit 1
}

while getopts "p:v:b:d:n:z:" opt; do
    case "$opt" in
        p) PROJECT_ID="$OPTARG" ;;
        v) GCSFUSE_VERSION="$OPTARG" ;;
        b) BUCKET_NAME="$OPTARG" ;;
        d) BQ_DATASET_ID="$OPTARG" ;;
        n) VM_NAME="$OPTARG" ;;
        z) VM_ZONE="$OPTARG" ;;
        *) usage ;;
    esac
done

if [ -z "$PROJECT_ID" ] || [ -z "$GCSFUSE_VERSION" ] || [ -z "$BUCKET_NAME" ] || [ -z "$BQ_DATASET_ID" ] || [ -z "$VM_NAME" ] || [ -z "$VM_ZONE" ]; then
    echo "Error: Missing required arguments."
    usage
fi

echo "=== Step 1: Environment Setup ==="
gcloud config set project "$PROJECT_ID"

echo "=== Step 2: Build Benchmark Images ==="
gcloud services enable artifactregistry.googleapis.com --project="$PROJECT_ID" || true
gcloud artifacts repositories create gcsfuse-benchmarks --repository-format=docker --location=us --project="$PROJECT_ID" || true
make build PROJECT="$PROJECT_ID" GCSFUSE_VERSION="$GCSFUSE_VERSION"
gcloud artifacts docker images list "us-docker.pkg.dev/$PROJECT_ID/gcsfuse-benchmarks"

echo "=== Step 3: Configure Target GCE VM ==="
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$PROJECT_ID" \
    --command="sudo apt-get update && sudo apt-get install -y util-linux python3 docker.io"
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$PROJECT_ID" \
    --command="sudo usermod -aG docker \$USER"
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$PROJECT_ID" \
    --command="gcloud auth configure-docker us-docker.pkg.dev --quiet"

echo "Copying benchmark scripts to VM..."
gcloud compute scp --recurse ../npi ../fio "${VM_NAME}:~" --zone="$VM_ZONE" --project="$PROJECT_ID"

echo "=== Step 4: Run Benchmarks ==="
echo "Executing npi.py remotely..."
gcloud compute ssh "$VM_NAME" --zone="$VM_ZONE" --project="$PROJECT_ID" \
    --command="cd npi && sg docker -c 'python3 npi.py --benchmarks all --bucket-name $BUCKET_NAME --project-id $PROJECT_ID --bq-dataset-id $BQ_DATASET_ID --gcsfuse-version $GCSFUSE_VERSION'"

echo "GCE Benchmarks Complete!"
