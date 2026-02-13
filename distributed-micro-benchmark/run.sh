#!/bin/bash
#
# Distributed Micro-Benchmarking Run Script
#
# Usage: run.sh [fio_job_file] [test_csv] [config_csv]

set -e
sudo apt-get update
echo "Installing git"
sudo apt-get install git
# --- Step 1. Python Environment Setup ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

cd "$SCRIPT_DIR"
VENV_DIR="${SCRIPT_DIR}/venv"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"

sudo apt install python3-venv -y
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
# Install dependencies if requirements.txt exists
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "Installing dependencies from $REQUIREMENTS_FILE..."
    pip install -q -r "$REQUIREMENTS_FILE"
else
    echo "ERROR: requirements.txt not found at $REQUIREMENTS_FILE"
    exit 1
fi

echo "Upgrading Google Cloud SDK to support 'gcloud storage'..."
# Remove the old apt-installed version to avoid conflicts
sudo apt-get remove -y google-cloud-sdk || true

# Add the official Google Cloud SDK distribution URI as a package source
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list

# Import the Google Cloud public key
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -

# Update and install the latest version
sudo apt-get update && sudo apt-get install -y google-cloud-cli

# Verify version
gcloud --version

# Step 2. Configurations
BENCHMARK_ID="benchmark-$(date +%s)"
REGIONAL_TEST_DATA_BUCKET="kokoro-regional-test-data-bucket-1"
ARTIFACTS_BUCKET="kokoro-perf-artifacts-bucket-1"
PROJECT="gcs-fuse-test"

INSTANCE_TEMPLATE_NAME="kokoro-perf-instance-template-1"
INSTANCE_GROUP_NAME="kokoro-perf-c4-standard-192-mig-1"
ZONE="us-central1-c"

FIO_JOB_FILE="${SCRIPT_DIR}/test_suites/kokoro/kokoro_fio_job.fio"
TEST_CSV="${SCRIPT_DIR}/test_suites/kokoro/kokoro_test_cases.csv"
CONFIGS_CSV="${SCRIPT_DIR}/test_suites/kokoro/kokoro_mount_configs.csv"

ITERATIONS=2
SEPARATE_CONFIGS=false # Set to true to generate separate CSV per config
POLL_INTERVAL=30
TIMEOUT=14400
GCSFUSE_COMMIT=master # do not modify this to take last days commit, this is under gcsfuse-tools repo. 

# Parse command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --commit) GCSFUSE_COMMIT="$2"; shift ;;
        *) ;; # Ignore other arguments or handle them as needed
    esac
    shift
done

echo "=========================================="
echo "Distributed Benchmark Configuration"
echo "=========================================="
echo "Benchmark ID: $BENCHMARK_ID"
echo "Instance Group: $INSTANCE_GROUP_NAME"
echo "Test CSV: $TEST_CSV"
echo "FIO Job File: $FIO_JOB_FILE"
echo "Test Data Bucket: $REGIONAL_TEST_DATA_BUCKET"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo "Zone: $ZONE"
echo "Project: $PROJECT"
echo "Iterations: $ITERATIONS"
echo "Configs CSV: $CONFIGS_CSV"
echo "GCSFuse Commit: $GCSFUSE_COMMIT"
echo "=========================================="
echo ""

# If GCSFUSE_COMMIT is set, update the configs CSV to use that commit
if [ -n "$GCSFUSE_COMMIT" ] && [ -f "$CONFIGS_CSV" ]; then
  echo "Updating $CONFIGS_CSV with commit $GCSFUSE_COMMIT..."
  sed -i "2,\$s|^[^,]*|$GCSFUSE_COMMIT|" "$CONFIGS_CSV"
fi

# --- STEP 3: Upload Scripts ---
echo "Uploading worker scripts to gs://${ARTIFACTS_BUCKET}/scripts/..."
gcloud storage rm -r "gs://${ARTIFACTS_BUCKET}/scripts/" 2> /dev/null || true
gcloud storage cp "${SCRIPT_DIR}"/workers/*.sh "gs://${ARTIFACTS_BUCKET}/scripts/"

# # --- STEP 4: Resize MIG to Start VMs ---
# echo "Resizing Instance Group ${INSTANCE_GROUP_NAME} to 3 instances..."
# gcloud compute instance-groups managed resize "${INSTANCE_GROUP_NAME}" \
#     --size=3 \
#     --zone="${ZONE}" \
#     --project="${PROJECT}"

# Wait for instances to be RUNNING
# echo "Waiting for 3 instances to be RUNNING..."
# for i in {1..30}; do
#     gcloud compute instance-groups managed list-instances "${INSTANCE_GROUP_NAME}" --zone="${ZONE}" --project="${PROJECT}" --format="table(instance.basename(), currentAction, instanceStatus)"
#     RUNNING_COUNT=$(gcloud compute instance-groups managed list-instances "${INSTANCE_GROUP_NAME}" --zone="${ZONE}" --project="${PROJECT}" --format="value(instanceStatus)" | grep -c "RUNNING" || true)
#     if [ "$RUNNING_COUNT" -ge 3 ]; then
#         echo "All 3 instances are RUNNING."
#         break
#     fi
#     echo "Currently $RUNNING_COUNT/3 instances running. Waiting 10s..."
#     sleep 10
# done

# --- DEBUG SECTION: Check Permissions ---
echo "=== DEBUG: Checking Permissions ==="
gcloud auth list
# Attempt to verify MIG existence before running Python script
if ! gcloud compute instance-groups managed describe "$INSTANCE_GROUP_NAME" \
    --project="$PROJECT" \
    --zone="$ZONE" > /dev/null 2>&1; then
    echo "ERROR: gcloud cannot see the Instance Group '$INSTANCE_GROUP_NAME'."
    echo "Please run this command locally to fix permissions:"
    echo "gcloud projects add-iam-policy-binding $PROJECT --member='serviceAccount:YOUR_SERVICE_ACCOUNT_EMAIL' --role='roles/compute.admin'"
    # We don't exit here to let the orchestrator try, but it will likely fail.
fi
echo "==================================="

# Pre-generate SSH keys so parallel workers don't crash trying to create them at the exact same time
ssh-keygen -t rsa -f ~/.ssh/google_compute_engine -N "" -q || true

# Tell the Python utils to use External IPs
export FORCE_EXTERNAL_IP=1

# --- STEP 5: Run Orchestrator ---
mkdir -p results
ORCHESTRATOR_CMD="python3 orchestrator.py \
 --benchmark-id $BENCHMARK_ID \
 --executor-vm $INSTANCE_GROUP_NAME \
 --zone $ZONE \
 --project $PROJECT \
 --artifacts-bucket $ARTIFACTS_BUCKET \
 --test-csv $TEST_CSV \
 --fio-job-file $FIO_JOB_FILE \
 --test-data-bucket $REGIONAL_TEST_DATA_BUCKET \
 --iterations $ITERATIONS \
 --poll-interval $POLL_INTERVAL \
 --timeout $TIMEOUT"

if [ -n "$CONFIGS_CSV" ] && [ -f "$CONFIGS_CSV" ]; then
 ORCHESTRATOR_CMD="$ORCHESTRATOR_CMD --configs-csv $CONFIGS_CSV"
fi

echo "Starting Orchestrator..."
echo "$ORCHESTRATOR_CMD"
eval $ORCHESTRATOR_CMD
echo ""
echo "=========================================="
echo "Benchmark Complete!"
echo "Benchmark ID: $BENCHMARK_ID"
echo "Results: results/${BENCHMARK_ID}/combined_report.csv"
echo "=========================================="

# --- STEP 6: Upload to BigQuery ---]
BQ_SCRIPT="$SCRIPT_DIR/helpers/upload_to_bq.py"
RESULTS_DIR="results/${BENCHMARK_ID}"

# Check if BQ script exists and results were generated
if [ -f "$BQ_SCRIPT" ] && [ -d "$RESULTS_DIR" ]; then
 echo ""
 echo "=========================================="
 echo "Uploading results to BigQuery..."
 
 # Check if running in Kokoro (env var usually set in CI)
 IS_KOKORO_FLAG=""
 if [ "${KOKORO_BUILD_ID:-}" != "" ]; then
  IS_KOKORO_FLAG="--is-kokoro"
 fi
 
 # Execute upload
 python3 "$BQ_SCRIPT" \
 --results-dir "$RESULTS_DIR" \
 --project-id "gcs-fuse-test-ml" \
 $IS_KOKORO_FLAG

else
 echo "Skipping BigQuery upload (Script '$BQ_SCRIPT' or Results dir '$RESULTS_DIR' not found)"
fi

# # --- STEP 7: Cleanup (Resize back to 0) ---
# echo "Cleaning up: Resizing Instance Group to 0..."
# gcloud compute instance-groups managed resize "${INSTANCE_GROUP_NAME}" \
#     --size=0 \
#     --zone="${ZONE}" \
#     --project="${PROJECT}"

echo "Benchmark Complete!"
