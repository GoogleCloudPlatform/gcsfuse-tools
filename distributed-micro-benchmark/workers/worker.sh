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
LOG_FILE="/tmp/worker_${BENCHMARK_ID}.log"

# Redirect output to log
exec > >(tee -a "$LOG_FILE") 2>&1

# Setup Workspace
WORKSPACE="/tmp/benchmark-${BENCHMARK_ID}"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# --- Import Modules ---
# Assumes modules are in the same directory as worker.sh or downloaded there
# For this example, we assume they are present in PWD
source ./setup.sh
source ./monitor.sh
source ./build.sh
source ./runner.sh

# Error handling
cleanup_gcsfuse() {
    echo "Cleaning up..." >&2
    if [ -n "${MOUNT_DIR:-}" ]; then
        fusermount -u "$MOUNT_DIR" 2>/dev/null || umount -f "$MOUNT_DIR" 2>/dev/null || true
    fi
    pkill -f "gcsfuse" || true
}

handle_error() {
    local exit_code=$1
    if [ $exit_code -ne 0 ] && [ ! -f "$WORKSPACE/.error_handled" ]; then
        touch "$WORKSPACE/.error_handled"
        cleanup_gcsfuse
        
        # Upload logs
        if [ -f "$LOG_FILE" ]; then
             gcloud storage cp "$LOG_FILE" "${LOG_BASE}/worker.log" 2>/dev/null || true
        fi
        
        # Update Manifest to failed
        if [ -f "manifest.json" ]; then
            END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
            jq ".status = \"failed\" | .end_time = \"$END_TIME\" | .error_code = $exit_code" manifest.json > manifest.json.tmp && mv manifest.json.tmp manifest.json
            gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json" || true
        fi
    fi
}
trap 'handle_error $?' ERR EXIT

# --- Main Flow ---

# 1. Install Deps
install_dependencies

# 2. Download Job Configs
echo "Downloading job configuration..."
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/jobs/${VM_NAME}.json" job.json
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/test-cases.csv" test-cases.csv
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/jobfile.fio" jobfile.fio
gcloud storage cp "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/config.json" config.json

# 3. Parse Config
MODE=$(jq -r '.mode // "single-config"' config.json)
BUCKET=$(jq -r '.bucket' job.json)
ITERATIONS=$(jq -r '.iterations' job.json)
MOUNT_DIR="$WORKSPACE/mnt"
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
TESTS_COMPLETED=0

if [ "$MODE" = "single-config" ]; then
    GCSFUSE_COMMIT=$(jq -r '.gcsfuse_commit // "master"' config.json)
    GCSFUSE_MOUNT_ARGS=$(jq -r '.gcsfuse_mount_args // ""' config.json)
    
    # Build Once
    GCSFUSE_BIN=$(build_gcsfuse_for_commit "$GCSFUSE_COMMIT")
    
    TEST_IDS=$(jq -r '.test_ids | join(" ")' job.json)
    for TEST_ID in $TEST_IDS; do
        if execute_test "$TEST_ID" "$TEST_ID" "$GCSFUSE_BIN" "$GCSFUSE_MOUNT_ARGS" "single"; then
            TESTS_COMPLETED=$((TESTS_COMPLETED + 1))
        fi
    done
else
    # Multi-config
    TEST_ENTRIES=$(jq -c '.test_entries[]' job.json)
    while IFS= read -r ENTRY; do
        MATRIX_ID=$(echo "$ENTRY" | jq -r '.matrix_id')
        TEST_ID=$(echo "$ENTRY" | jq -r '.test_id')
        COMMIT=$(echo "$ENTRY" | jq -r '.commit')
        MOUNT_ARGS=$(echo "$ENTRY" | jq -r '.mount_args')
        CONFIG_ID=$(echo "$ENTRY" | jq -r '.config_id')
        CONFIG_LABEL=$(echo "$ENTRY" | jq -r '.config_label')
        
        GCSFUSE_BIN=$(build_gcsfuse_for_commit "$COMMIT")
        
        if execute_test "$TEST_ID" "$MATRIX_ID" "$GCSFUSE_BIN" "$MOUNT_ARGS" "multi" "$MATRIX_ID" "$CONFIG_ID" "$CONFIG_LABEL" "$COMMIT"; then
            TESTS_COMPLETED=$((TESTS_COMPLETED + 1))
        fi
    done < <(echo "$TEST_ENTRIES")
fi

# 6. Finalize
if [ "$MODE" = "single-config" ]; then
    TOTAL_TESTS=$(echo "$TEST_IDS" | wc -w)
else
    TOTAL_TESTS=$(echo "$TEST_ENTRIES" | wc -l)
fi

END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
STATUS="completed"
if [ $TESTS_COMPLETED -lt $TOTAL_TESTS ]; then STATUS="partial"; fi

jq ".status = \"$STATUS\" | .end_time = \"$END_TIME\" | .total_tests = $TOTAL_TESTS | .completed_tests = $TESTS_COMPLETED" manifest.json > manifest.final.json
mv manifest.final.json manifest.json
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

trap - ERR EXIT
echo "Done."