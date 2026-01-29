#!/bin/bash
#
# Combined E2E Worker Test (Inspired by test-worker-local.sh)
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

echo "=========================================="
echo "Starting E2E Worker Test"
echo "=========================================="
echo "Benchmark ID: $BENCHMARK_ID"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo ""

# --- 1. Infrastructure Setup ---
echo "Creating GCS Bucket: gs://${ARTIFACTS_BUCKET}..."
gcloud storage buckets create "gs://${ARTIFACTS_BUCKET}" --project="${PROJECT_ID}"

cleanup() {
    echo ""
    echo "=========================================="
    echo "SUCCESS: Deleting bucket gs://${ARTIFACTS_BUCKET}"
    echo "=========================================="
    gcloud storage rm --recursive "gs://${ARTIFACTS_BUCKET}"
    rm -f test-cases.csv jobfile.fio config.json "${VM_NAME}.json"
}

# --- 2. Preparing Configuration Files ---
# CSV Header must match runner.sh: io_type,threads,file_size,block_size,io_depth,nr_files
cat <<EOF > test-cases.csv
io_type,num_jobs,file_size,block_size,io_depth,nr_files
read,1,10m,1m,1,1
EOF

cat <<EOF > jobfile.fio
[global]
ioengine=libaio
direct=0
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
  "mode": "single-config",
  "iterations": 1,
  "bucket": "${ARTIFACTS_BUCKET}",
  "gcsfuse_commit": "master",
  "gcsfuse_mount_args": "--implicit-dirs"
}
EOF

cat <<EOF > "${VM_NAME}.json"
{
  "bucket": "${ARTIFACTS_BUCKET}",
  "iterations": 1,
  "test_ids": [0]
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
