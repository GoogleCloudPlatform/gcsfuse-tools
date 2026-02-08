#!/bin/bash
set -e

# --- 0. Set Working Directory ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# --- 1. Configuration ---
TIMESTAMP=$(date +%s)
PROJECT_ID=$(gcloud config get-value project)
ZONE="us-west1-a"
REGION="us-west1"
BUCKET_NAME="single-vm-test-bucket-${TIMESTAMP}"
BENCHMARK_ID="single-vm-e2e-${TIMESTAMP}"
VM_NAME="test-vm-${TIMESTAMP}"

echo "--- 1. Infrastructure Setup ---"

# Create Bucket for artifacts and results
gcloud storage buckets create "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" --location="${REGION}"

# Upload modular worker scripts to GCS so the orchestrator can trigger them
echo "Uploading modular worker scripts to GCS..."
gcloud storage cp workers/*.sh "gs://${BUCKET_NAME}/${BENCHMARK_ID}/scripts/"

# Create a Single VM Instance
echo "Creating Single VM Instance..."
gcloud compute instances create "${VM_NAME}" \
  --machine-type=n2-standard-4 \
  --zone="${ZONE}" \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --scopes=cloud-platform \
  --metadata=startup-script="#!/bin/bash
  # Download all modular scripts to /tmp for execution
  gcloud storage cp gs://${BUCKET_NAME}/${BENCHMARK_ID}/scripts/*.sh /tmp/
  chmod +x /tmp/*.sh"

# Wait for VM to initialize and complete startup script
echo "Waiting for VM to initialize..."
sleep 60

echo "--- 2. Running Orchestrator ---"
# Targeting a single VM name
python3 orchestrator.py \
  --benchmark-id "${BENCHMARK_ID}" \
  --executor-vm "${VM_NAME}" \
  --zone "${ZONE}" \
  --project "${PROJECT_ID}" \
  --artifacts-bucket "${BUCKET_NAME}" \
  --test-csv "test_suites/test_orchestrator/test_cases_sample.csv" \
  --configs-csv "test_suites/test_orchestrator/mount_configs.csv" \
  --fio-job-file "test_suites/test_orchestrator/fio_job_default.fio" \
  --test-data-bucket "${BUCKET_NAME}" \
  --iterations 1 \
  --poll-interval 20 \
  --timeout 600

echo ""
echo "--- 3. Verification & Cleanup ---"
echo "Benchmark finished successfully. Cleaning up resources..."

# Delete the Instance
gcloud compute instances delete "${VM_NAME}" --zone="${ZONE}" --quiet

# Delete the Bucket and all artifacts
gcloud storage rm --recursive "gs://${BUCKET_NAME}"

echo "Cleanup complete."
