#!/bin/bash
#
# Distributed Micro-Benchmarking Run Script
#
# Usage: ./run.sh [--commit <gcsfuse_commit_hash>]

set -e

# --- Step 1. Environment Setup ---
if ! command -v git &> /dev/null; then
    sudo apt-get update
    echo "Installing git"
    sudo apt-get install -y git
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"
VENV_DIR="${SCRIPT_DIR}/venv"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"

if [ ! -d "$VENV_DIR" ]; then
    sudo apt install python3-venv -y
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

# Install dependencies if requirements.txt exists
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "Installing dependencies from $REQUIREMENTS_FILE..."
    pip install -q -r "$REQUIREMENTS_FILE"
else
    echo "ERROR: requirements.txt not found at $REQUIREMENTS_FILE"
    exit 1
fi

# Update and install gcloud
if ! command -v gcloud &> /dev/null; then
    sudo apt-get install -y google-cloud-cli
    gcloud --version
fi

# Step 2. Configurations
BENCHMARK_ID="benchmark-$(date +%s)"
REGIONAL_TEST_DATA_BUCKET="grpc-metric-dmb-regional"
ARTIFACTS_BUCKET="dmb-artifacts-regional"
PROJECT="gcs-fuse-test"

INSTANCE_GROUP_NAME="dmb-instance-group"
ZONE="us-central1-c"

FIO_JOB_FILE="${SCRIPT_DIR}/test_suites/published_benchmarks/fio_job.fio"
TEST_CSV="${SCRIPT_DIR}/test_suites/published_benchmarks/published_test_cases.csv"
CONFIGS_CSV="${SCRIPT_DIR}/test_suites/published_benchmarks/mount_configs.csv"

ITERATIONS=2
SEPARATE_CONFIGS=false # Set to true to generate separate CSV per config
POLL_INTERVAL=60
TIMEOUT=14400 # 4 hours
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

# --- STEP 4: Provide permissions ---
# 1. Create .ssh directory and force correct permissions (700 is mandatory)
mkdir -p ~/.ssh
sudo chown -R $(whoami):$(whoami) ~/.ssh
chmod 700 ~/.ssh

# 2. Create known_hosts with correct permissions
touch ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts

# 3. Pre-generate the SSH key so gcloud doesn't ask interactively
if [ ! -f ~/.ssh/google_compute_engine ]; then
    echo "Generating SSH key..."
    ssh-keygen -t rsa -f ~/.ssh/google_compute_engine -N "" -q
fi

# 4. Force gcloud to respect these keys
gcloud config set compute/zone $ZONE
gcloud config set project $PROJECT

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
 
    # Execute upload
    python3 "$BQ_SCRIPT" \
    --results-dir "$RESULTS_DIR" \
    --project-id "$PROJECT"

fi
echo "Benchmark Complete!"
