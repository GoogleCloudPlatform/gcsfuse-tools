#!/bin/bash
#
# Combined E2E Local Worker Test
#

set -e

# ============================================
# CONFIGURATION
# ============================================
TIMESTAMP=$(date +%s)
PROJECT_ID=$(gcloud config get-value project)
ARTIFACTS_BUCKET="worker-test-${TIMESTAMP}"
BENCHMARK_ID="e2e-manual-test-${TIMESTAMP}"
VM_NAME=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name || echo "cpranjal-vm-1")
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
RUN_DIR="$(dirname "$SCRIPT_DIR")/worker_test_configs/${BENCHMARK_ID}"
mkdir -p "$RUN_DIR"
cd "$RUN_DIR"

echo "=========================================="
echo "Starting E2E Worker Test"
echo "=========================================="
echo "Benchmark ID: $BENCHMARK_ID"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo "Config folder: $RUN_DIR"
echo ""

# --- 1. Infrastructure Setup ---
echo "Creating GCS Bucket: gs://${ARTIFACTS_BUCKET}..."
gcloud storage buckets create "gs://${ARTIFACTS_BUCKET}" --project="${PROJECT_ID}"

cleanup() {
    echo ""
    echo "=========================================="
    echo "SUCCESS: Cleaning up gs://${ARTIFACTS_BUCKET}"
    echo "=========================================="
    gcloud storage rm --recursive "gs://${ARTIFACTS_BUCKET}"
}

# --- 2. Preparing Configuration Files ---
cat <<EOF > test-cases.csv
io_type,num_jobs,file_size,block_size,io_depth,nr_files,direct
read,1,10m,1m,1,1,0
EOF

cat <<EOF > jobfile.fio
[global]
ioengine=libaio
direct=\$DIRECT
verify=0
bs=\$BS
iodepth=\$IO_DEPTH
nrfiles=\$NRFILES
group_reporting=1

[test]
rw=\$IO_TYPE
filesize=\$FILE_SIZE
directory=\$TEST_DATA_DIR
numjobs=\$THREADS
EOF

cat <<EOF > config.json
{
  "iterations": 1,
  "bucket": "${ARTIFACTS_BUCKET}"
}
EOF

# FIXED: Updated to use 'test_entries' to satisfy the jq requirement in worker.sh
cat <<EOF > "${VM_NAME}.json"
{
  "bucket": "${ARTIFACTS_BUCKET}",
  "iterations": 1,
  "test_entries": [
    {
      "matrix_id": 0,
      "test_id": 0,
      "config_id": 0,
      "config_label": "default",
      "commit": "master",
      "mount_args": "--implicit-dirs"
    }
  ]
}
EOF

# --- 3. Uploading to GCS ---
BASE_GCS="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}"
gcloud storage cp test-cases.csv "${BASE_GCS}/test-cases.csv"
gcloud storage cp jobfile.fio "${BASE_GCS}/jobfile.fio"
gcloud storage cp config.json "${BASE_GCS}/config.json"
gcloud storage cp "${VM_NAME}.json" "${BASE_GCS}/jobs/${VM_NAME}.json"

# --- 4. Running Worker ---
chmod +x "$SCRIPT_DIR"/*.sh
if "$SCRIPT_DIR/worker.sh" "$BENCHMARK_ID" "$ARTIFACTS_BUCKET"; then
    # Verify manifest in GCS before cleanup
    RESULT_DIR="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/results/${VM_NAME}"
    if gcloud storage ls "${RESULT_DIR}/manifest.json" &>/dev/null; then
        cleanup
    else
        echo "ERROR: manifest.json missing in GCS!"
        exit 1
    fi
else
    echo "ERROR: Worker script failed. Bucket preserved for debugging: gs://${ARTIFACTS_BUCKET}"
    exit 1
fi
