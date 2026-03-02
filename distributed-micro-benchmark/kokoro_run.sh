#!/bin/bash
#
# Distributed Micro-Benchmarking Run Script
#
# Usage: ./run.sh [--commit <gcsfuse_commit_hash>] [--read] [--write]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

BENCHMARK_ID="benchmark-$(date +%s)"
REGIONAL_TEST_DATA_BUCKET="kokoro-regional-test-data-bucket"
ARTIFACTS_BUCKET="kokoro-perf-artifacts-bucket"
PROJECT="gcs-fuse-test-ml"
INSTANCE_GROUP_NAME="kokoro-perf-c4-standard-192-mig"
ZONE="us-central1-c"

READ_CONFIGS_CSV="${SCRIPT_DIR}/test_suites/kokoro/kokoro_read_mount_configs.csv"
READ_FIO_JOB_FILE="${SCRIPT_DIR}/test_suites/kokoro/kokoro_read_fio_job.fio"
READ_TEST_CSV="${SCRIPT_DIR}/test_suites/kokoro/kokoro_read_test_cases.csv"

WRITE_CONFIGS_CSV="${SCRIPT_DIR}/test_suites/kokoro/kokoro_write_mount_configs.csv"
WRITE_FIO_JOB_FILE="${SCRIPT_DIR}/test_suites/kokoro/kokoro_write_fio_job.fio"
WRITE_TEST_CSV="${SCRIPT_DIR}/test_suites/kokoro/kokoro_write_test_cases.csv"

ITERATIONS=2
SEPARATE_CONFIGS=false # Set to true to generate separate CSV per config
POLL_INTERVAL=60
TIMEOUT=14400 # 4 hours
GCSFUSE_COMMIT=master 
RUN_READ=false
RUN_WRITE=false

SINGLE_THREAD_VM_TYPE="kokoro-perf-instance-template-n2-standard-32-single-threaded"
MULTI_THREAD_VM_TYPE="kokoro-perf-instance-template"


# Parse command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --commit) GCSFUSE_COMMIT="$2"; shift ;;
        --read) RUN_READ=true ;;
        --write) RUN_WRITE=true ;;
        *) ;; # Ignore other arguments or handle them as needed
    esac
    shift
done

# If neither read nor write is specified, run both (default behavior)
if [ "$RUN_READ" = false ] && [ "$RUN_WRITE" = false ]; then
    RUN_READ=true
    RUN_WRITE=true
fi

echo "=========================================="
echo "Distributed Benchmark Configuration"
echo "=========================================="
echo "Benchmark ID: $BENCHMARK_ID"
echo "Instance Group: $INSTANCE_GROUP_NAME"
echo "Test Data Bucket: $REGIONAL_TEST_DATA_BUCKET"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo "Zone: $ZONE"
echo "Project: $PROJECT"
echo "Iterations: $ITERATIONS"
echo "Read Configs CSV: $READ_CONFIGS_CSV"
echo "Write Configs CSV: $WRITE_CONFIGS_CSV"
echo "Read FIO Job File: $READ_FIO_JOB_FILE"
echo "Write FIO Job File: $WRITE_FIO_JOB_FILE"
echo "Read Test CSV: $READ_TEST_CSV"
echo "Write Test CSV: $WRITE_TEST_CSV"
echo "GCSFuse Commit: $GCSFUSE_COMMIT"
echo "=========================================="
echo ""

setup_environment() {
    echo "--- Step 1. Environment Setup ---"
    if ! command -v git &> /dev/null; then
        sudo apt-get update
        echo "Installing git"
        sudo apt-get install -y git
    fi

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
    
    echo "Upgrading Google Cloud SDK to support 'gcloud storage'..."
    # Remove the old apt-installed version to avoid conflicts
    sudo apt-get remove -y google-cloud-sdk || true
    # Add the official Google Cloud SDK distribution URI as a package source
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
    # Import the Google Cloud public key
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -

    # Update and install gcloud
    sudo apt-get update && sudo apt-get install -y google-cloud-cli
    gcloud --version
}

update_commit_hashes() {
    # If GCSFUSE_COMMIT is set, update the configs CSVs to use that commit
    echo "--- STEP 2: Update Commit Hashes---"
    for CSV in "$READ_CONFIGS_CSV" "$WRITE_CONFIGS_CSV"; do
      if [ -n "$GCSFUSE_COMMIT" ] && [ -f "$CSV" ]; then
        echo "Updating $CSV with commit $GCSFUSE_COMMIT..."
        sed -i "2,\$s|^[^,]*|$GCSFUSE_COMMIT|" "$CSV"
      fi
    done
}

upload_scripts() {
    echo "--- STEP 3: Upload Scripts ---"
    echo "Uploading worker scripts to gs://${ARTIFACTS_BUCKET}/scripts/..."
    gcloud storage rm -r "gs://${ARTIFACTS_BUCKET}/scripts/" 2> /dev/null || true
    gcloud storage cp "${SCRIPT_DIR}"/workers/*.sh "gs://${ARTIFACTS_BUCKET}/scripts/"
}

setup_permissions() {
    echo "--- STEP 4: Provide permissions ---"
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
}

run_benchmark() {
    local TYPE=$1
    local FIO_JOB_FILE=$2
    local TEST_CSV=$3
    local CONFIGS_CSV=$4
    local CURRENT_BENCHMARK_ID="${BENCHMARK_ID}-${TYPE}"
    local REPORT_NAME="${TYPE}_combined_report.csv"

    echo "=========================================="
    echo "Running $TYPE Benchmark"
    echo "Benchmark ID: $CURRENT_BENCHMARK_ID"
    echo "FIO Job File: $FIO_JOB_FILE"
    echo "Test CSV: $TEST_CSV"
    echo "Configs CSV: $CONFIGS_CSV"
    echo "=========================================="

    # --- STEP 5: Run Orchestrator ---
    mkdir -p results
    ORCHESTRATOR_CMD="python3 orchestrator.py \
     --benchmark-id $CURRENT_BENCHMARK_ID \
     --executor-vm $INSTANCE_GROUP_NAME \
     --zone $ZONE \
     --project $PROJECT \
     --artifacts-bucket $ARTIFACTS_BUCKET \
     --test-csv $TEST_CSV \
     --fio-job-file $FIO_JOB_FILE \
     --test-data-bucket $REGIONAL_TEST_DATA_BUCKET \
     --iterations $ITERATIONS \
     --poll-interval $POLL_INTERVAL \
     --timeout $TIMEOUT \
     --report-name $REPORT_NAME \
     --single-thread-vm-type='$SINGLE_THREAD_VM_TYPE' \
     --multi-thread-vm-type='$MULTI_THREAD_VM_TYPE'"

    if [ -n "$CONFIGS_CSV" ] && [ -f "$CONFIGS_CSV" ]; then
     ORCHESTRATOR_CMD="$ORCHESTRATOR_CMD --configs-csv $CONFIGS_CSV"
    fi

    echo "Starting Orchestrator..."
    echo "$ORCHESTRATOR_CMD"
    eval $ORCHESTRATOR_CMD
    
    echo ""
    echo "=========================================="
    echo "Benchmark $TYPE Complete!"
    echo "Results: results/${CURRENT_BENCHMARK_ID}/${REPORT_NAME}"
    echo "=========================================="

    # --- STEP 6: Upload to BigQuery ---
    BQ_SCRIPT="$SCRIPT_DIR/helpers/upload_to_bq.py"
    RESULTS_DIR="results/${CURRENT_BENCHMARK_ID}"

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
        --project-id "$PROJECT" \
        --report-name "$REPORT_NAME" || echo "WARNING: BigQuery upload failed. Continuing..." \
        $IS_KOKORO_FLAG
    fi
}

setup_environment
update_commit_hashes
upload_scripts
setup_permissions

if [ "$RUN_READ" = true ]; then
    run_benchmark "read" "$READ_FIO_JOB_FILE" "$READ_TEST_CSV" "$READ_CONFIGS_CSV"
fi

if [ "$RUN_WRITE" = true ]; then
    # Run write benchmark in a loop to ensure new directories are used for each iteration
    (
        original_benchmark_id="$BENCHMARK_ID"
        total_iterations=$ITERATIONS
        for ((i=1; i<=total_iterations; i++)); do
            BENCHMARK_ID="${original_benchmark_id}-iter${i}"
            ITERATIONS=1
            run_benchmark "write" "$WRITE_FIO_JOB_FILE" "$WRITE_TEST_CSV" "$WRITE_CONFIGS_CSV"
        done
    )
fi

echo "Benchmark Complete!"
