#!/bin/bash
#
# Distributed Micro-Benchmarking Run Script
#

set -e

# Function to create FIO config file
create_fio_config() {
    local output_file="$1"
    cat > "$output_file" << 'FIO_CONFIG_EOF'
[global]
ioengine=libaio
direct=0
verify=0
bs=BLOCK_SIZE
iodepth=IO_DEPTH
runtime=120s
time_based=0
fadvise_hint=0
nrfiles=NR_FILES
thread=1
openfiles=1
group_reporting=1
filename_format=test.$jobnum.$filenum

[test]
rw=IO_TYPE
filesize=FILE_SIZE
directory=MOUNT_POINT
numjobs=NUM_JOBS
FIO_CONFIG_EOF
    echo "Created FIO config: $output_file"
}

# Function to create test cases CSV
create_test_cases() {
    local output_file="$1"
    cat > "$output_file" << 'TEST_CSV_EOF'
block_size,file_size,io_depth,io_type,num_jobs,nr_files
4k,1g,32,read,1,1
128k,1g,64,read,1,1
TEST_CSV_EOF
    echo "Created test cases: $output_file"
}

# Configuration - EDIT THESE VALUES
BENCHMARK_ID="benchmark-$(date +%s)"
INSTANCE_GROUP="princer-test"
TEST_CSV="sample-tests.csv"
FIO_JOB_FILE="jobfile.fio"
BUCKET="princer-zonal-us-west4-a"
ARTIFACTS_BUCKET="princer-working-dirs"
ZONE="us-west4-a"
PROJECT="gcs-tess"
ITERATIONS=1
GCSFUSE_COMMIT="master"

# Advanced options
POLL_INTERVAL=30
TIMEOUT=7200

echo "=========================================="
echo "Distributed Benchmark Configuration"
echo "=========================================="
echo "Benchmark ID: $BENCHMARK_ID"
echo "Instance Group: $INSTANCE_GROUP"
echo "Test CSV: $TEST_CSV"
echo "FIO Job File: $FIO_JOB_FILE"
echo "Bucket: $BUCKET"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo "Zone: $ZONE"
echo "Project: $PROJECT"
echo "Iterations: $ITERATIONS"
echo "GCSFuse Commit: $GCSFUSE_COMMIT"
echo "=========================================="
echo ""

# Generate FIO job file if it doesn't exist
if [ ! -f "$FIO_JOB_FILE" ]; then
    echo "FIO job file not found. Creating default: $FIO_JOB_FILE"
    create_fio_config "$FIO_JOB_FILE"
fi

# Generate test CSV if it doesn't exist
if [ ! -f "$TEST_CSV" ]; then
    echo "Test CSV not found. Creating default: $TEST_CSV"
    create_test_cases "$TEST_CSV"
fi

# Create results directory
mkdir -p results

# Upload config to GCS
CONFIG_JSON=$(mktemp)
cat > "$CONFIG_JSON" <<EOF
{
  "gcsfuse_commit": "$GCSFUSE_COMMIT",
  "iterations": $ITERATIONS,
  "bucket": "$BUCKET"
}
EOF

echo "Uploading config..."
gcloud storage cp "$CONFIG_JSON" "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/config.json"
rm "$CONFIG_JSON"

# Run orchestrator
echo ""
python3 orchestrator.py \
    --benchmark-id "$BENCHMARK_ID" \
    --instance-group "$INSTANCE_GROUP" \
    --test-csv "$TEST_CSV" \
    --fio-job-file "$FIO_JOB_FILE" \
    --bucket "$BUCKET" \
    --artifacts-bucket "$ARTIFACTS_BUCKET" \
    --zone "$ZONE" \
    --project "$PROJECT" \
    --iterations "$ITERATIONS" \
    --poll-interval "$POLL_INTERVAL" \
    --timeout "$TIMEOUT"

echo ""
echo "=========================================="
echo "Benchmark Complete!"
echo "Benchmark ID: $BENCHMARK_ID"
echo "Results: results/${BENCHMARK_ID}_report.txt"
echo "=========================================="

