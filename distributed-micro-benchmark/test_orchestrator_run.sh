#!/bin/bash
set -e

# Install dependencies
sudo apt-get install python3-tabulate

# --- 0. Set Working Directory ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# --- 1. Configuration ---
TIMESTAMP=$(date +%s)
PROJECT_ID=$(gcloud config get-value project)
ZONE="us-west1-a"
REGION="us-west1"
BUCKET_NAME="orchestrator-test-bucket-${TIMESTAMP}"
BENCHMARK_ID="orch-e2e-${TIMESTAMP}"
INSTANCE_TEMPLATE="orch-test-template-${TIMESTAMP}"
INSTANCE_GROUP="orch-test-group-${TIMESTAMP}"

echo "--- 1. Infrastructure Setup ---"

# Create Bucket
gcloud storage buckets create "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" --location="${REGION}"

# Upload modular scripts to GCS ---
# Now this works because we are in the script's directory
echo "Uploading modular worker scripts to GCS..."
gcloud storage cp workers/*.sh "gs://${BUCKET_NAME}/${BENCHMARK_ID}/scripts/"

# Create Instance Template 
echo "Creating Instance Template..."
gcloud compute instance-templates create "${INSTANCE_TEMPLATE}" \
    --machine-type=n2-standard-4 \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --scopes=cloud-platform

# Create Managed Instance Group (MIG) with 2 VMs
gcloud compute instance-groups managed create "${INSTANCE_GROUP}" \
    --template="${INSTANCE_TEMPLATE}" \
    --size=2 \
    --zone="${ZONE}"

# Wait for VMs to be "RUNNING"
echo "Waiting for VMs to initialize..."
sleep 90

echo "--- 2. Running Orchestrator ---"
python3 orchestrator.py \
    --benchmark-id "${BENCHMARK_ID}" \
    --executor-vm "${INSTANCE_GROUP}" \
    --zone "${ZONE}" \
    --project "${PROJECT_ID}" \
    --artifacts-bucket "${BUCKET_NAME}" \
    --test-csv "test_suites/test_orchestrator/test_cases_sample.csv" \
    --configs-csv "test_suites/test_orchestrator/test_mount_configs.csv" \
    --fio-job-file "test_suites/test_orchestrator/test_read.fio" \
    --test-data-bucket "${BUCKET_NAME}" \
    --iterations 1 \
    --poll-interval 20 \
    --timeout 1000

# --- 3. Cleanup (Runs only on Success) ---
echo ""
echo "--- 3. Verification & Cleanup ---"
echo "Benchmark finished successfully. Cleaning up resources..."

# Delete the Managed Instance Group
# We use --quiet to avoid interactive prompts
gcloud compute instance-groups managed delete "${INSTANCE_GROUP}" \
    --zone="${ZONE}" \
    --quiet

# Delete the Instance Template
gcloud compute instance-templates delete "${INSTANCE_TEMPLATE}" --quiet

# Delete the Bucket and all artifacts
gcloud storage rm --recursive "gs://${BUCKET_NAME}"

echo "Cleanup complete."
