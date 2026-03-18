#!/bin/bash
# Distributed Micro-Benchmarking Worker

set -e

BENCHMARK_ID="$1"
ARTIFACTS_BUCKET="$2"

if [ -z "$BENCHMARK_ID" ] || [ -z "$ARTIFACTS_BUCKET" ]; then
  echo "Usage: $0 <benchmark_id> <artifacts_bucket>"
  exit 1
fi

VM_NAME=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name)
RESULT_BASE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/results/${VM_NAME}"
LOG_BASE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/logs/${VM_NAME}"

echo "VM: $VM_NAME"
echo "Benchmark ID: $BENCHMARK_ID"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo "Start time: $(date)"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
LOG_FILE="${SCRIPT_DIR}/worker_${BENCHMARK_ID}.log"

echo "VM: $VM_NAME"

# --- Import Modules using the absolute path ---
source "$SCRIPT_DIR/setup.sh"
source "$SCRIPT_DIR/monitor.sh"
source "$SCRIPT_DIR/build.sh"
source "$SCRIPT_DIR/runner.sh"

# Redirect output to log
exec > >(tee -a "$LOG_FILE") 2>&1

# Setup Workspace
WORKSPACE="$HOME/benchmark-${BENCHMARK_ID}"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

MOUNT_DIR="$WORKSPACE/mnt"

# Error handling
cleanup_gcsfuse() {
    echo "Cleaning up GCSFuse/FIO mounts and processes..." >&2
    
    # Unmount if mounted
    if [ -n "${MOUNT_DIR:-}" ] && mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
        echo "  Unmounting $MOUNT_DIR..." >&2
        sudo fusermount -u "$MOUNT_DIR" 2>/dev/null || sudo umount -f "$MOUNT_DIR" 2>/dev/null || true
        sleep 1
    fi
    
    # Kill any lingering GCSFuse and FIO processes aggressively
    echo "  Killing any orphaned GCSFuse and FIO processes..." >&2
    sudo pkill -9 -f gcsfuse 2>/dev/null || true
    sudo pkill -9 -f fio 2>/dev/null || true
}

handle_error() {
    local exit_code=$1
    
    # Only handle non-zero exit codes and avoid double-handling
    if [ $exit_code -ne 0 ] && [ ! -f "$WORKSPACE/.error_handled" ]; then
        touch "$WORKSPACE/.error_handled"
        echo "ERROR: Script failed with exit code $exit_code"
        cleanup_gcsfuse
        
        # Stop cancellation monitor if running
        if [ -n "$CANCEL_CHECK_PID" ]; then
            kill $CANCEL_CHECK_PID 2>/dev/null || true
        fi
        
        # Check if cancellation caused the failure
        CANCEL_FLAG="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/cancel"
        CANCELLED=false
        if gcloud storage ls "$CANCEL_FLAG" > /dev/null 2>&1; then
            CANCELLED=true
            STATUS="cancelled"
            echo "Job was cancelled"
        else
            STATUS="failed"
        fi
        
        # Upload logs if available
        if [ -n "$LOG_BASE" ] && [ -f "$LOG_FILE" ]; then
            gcloud storage cp "$LOG_FILE" "${LOG_BASE}/worker.log" 2>/dev/null || true
        fi
        
        # Update manifest to failed/cancelled status if it exists
        if [ -f "$WORKSPACE/manifest.json" ]; then
            END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
            jq ".status = \"$STATUS\" | .end_time = \"$END_TIME\" | .error_code = $exit_code" manifest.json > manifest_tmp.json
            mv manifest_tmp.json manifest.json
            
            # Upload manifest
            if [ -n "$RESULT_BASE" ]; then
                gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json" || true
            fi
        fi
        
        if $CANCELLED; then
            echo "✓ Job cancelled gracefully"
        else
            echo "✗ Worker failed"
        fi
    fi
}
trap 'handle_error $?' ERR EXIT

# --- Main Flow ---

# 0. Pre-flight cleanup of any existing orphaned processes from previous runs
cleanup_gcsfuse

# 1. Install Deps
install_dependencies

# 2. Download Job Configs
echo "Downloading job configuration..."
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/jobs/${VM_NAME}.json" job.json
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/config.json" config.json

# Read filenames from config.json (fallback to defaults if missing)
TEST_FILENAME=$(jq -r '.test_filename // "test-cases.csv"' config.json)
JOB_FILENAME=$(jq -r '.job_filename // "jobfile.fio"' config.json)

gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/${TEST_FILENAME}" test-cases.csv
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/${JOB_FILENAME}" jobfile.fio

# 3. Parse Config
BUCKET=$(jq -r '.bucket' job.json)
ITERATIONS=$(jq -r '.iterations' job.json)

echo "Test bucket: $BUCKET"
echo "Iterations: $ITERATIONS"

mkdir -p "$MOUNT_DIR"

# 4. Initialize Manifest
cat > manifest.json <<EOF
{
  "vm_name": "$VM_NAME",
  "status": "running",
  "start_time": "$(date -u +"%Y-%m-%dT%H:%M:%S%z")",
  "tests": []
}
EOF
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

# 5. Run Tests
TEST_ENTRIES=$(jq -c '.test_entries[]' job.json)
TESTS_COMPLETED=0
TOTAL_TESTS=$(echo "$TEST_ENTRIES" | wc -l)

while IFS= read -r ENTRY; do
    MATRIX_ID=$(echo "$ENTRY" | jq -r '.matrix_id')
    TEST_ID=$(echo "$ENTRY" | jq -r '.test_id')
    COMMIT=$(echo "$ENTRY" | jq -r '.commit')
    MOUNT_ARGS=$(echo "$ENTRY" | jq -r '.mount_args')
    CONFIG_ID=$(echo "$ENTRY" | jq -r '.config_id')
    CONFIG_LABEL=$(echo "$ENTRY" | jq -r '.config_label')

    # Build (Cached inside build_gcsfuse_for_commit)
    GCSFUSE_BIN=$(build_gcsfuse_for_commit "$COMMIT")

    if execute_test "$TEST_ID" "$MATRIX_ID" "$GCSFUSE_BIN" "$MOUNT_ARGS" "$MATRIX_ID" "$CONFIG_ID" "$CONFIG_LABEL" "$COMMIT"; then
        TESTS_COMPLETED=$((TESTS_COMPLETED + 1))
    fi
done < <(echo "$TEST_ENTRIES")

# 6. Finalize
STATUS="completed"
if [ $TESTS_COMPLETED -lt $TOTAL_TESTS ]; then STATUS="failed"; fi

END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
jq ".status = \"$STATUS\" | .end_time = \"$END_TIME\" | .total_tests = $TOTAL_TESTS | .completed_tests = $TESTS_COMPLETED" manifest.json > manifest.final.json
mv manifest.final.json manifest.json
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

# Disable error trap before final cleanup
trap - ERR EXIT

# 7. Cleanup: Delete the remote workspace directory if the script succeeded
echo "Cleaning up workspace: $WORKSPACE"
# cd "$HOME"
# # Handle read-only files (like Go cache)
# chmod -R +w . 2>/dev/null || true
# find . -mindepth 1 -delete

echo "Done."
