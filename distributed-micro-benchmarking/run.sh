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
bs=$BS
iodepth=$IO_DEPTH
runtime=120s
time_based=0
fadvise_hint=0
nrfiles=$NRFILES
thread=1
openfiles=1
group_reporting=1
filename_format=test.$jobnum.$filenum

[test]
rw=$IO_TYPE
filesize=$FILE_SIZE
directory=$TEST_DATA_DIR
numjobs=$THREADS
FIO_CONFIG_EOF
    echo "Created FIO config: $output_file"
}

# Function to create test cases CSV
create_test_cases() {
    local output_file="$1"
    cat > "$output_file" << 'TEST_CSV_EOF'
block_size,file_size,io_depth,io_type,num_jobs,nr_files
1m,1g,1,randread,96,20
1m,1m,1,randread,96,200
TEST_CSV_EOF
    echo "Created test cases: $output_file"
}

# Additional test cases (uncomment to add more):
# 1m,10g,1,randread,96,4
# 1m,16m,1,randread,96,120
# 1m,32m,1,randread,96,100
# 1m,64m,1,randread,96,100
# 64k,64k,1,randread,96,400
# 256k,256k,1,randread,96,400
# 1m,2g,1,randread,96,10
# 1m,4m,1,randread,96,150
# 1m,5g,1,randread,96,10
# 1m,8m,1,randread,96,120

# Function to create configs CSV
create_configs() {
    local output_file="$1"
    cat > "$output_file" << 'CONFIGS_CSV_EOF'
commit,mount_args,label
go_new_mrd,"--stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 --metadata-cache-ttl-secs=2000 --enable-kernel-reader=false",agareader
go_new_mrd,"--stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 --metadata-cache-ttl-secs=2000",simplereader
CONFIGS_CSV_EOF
    echo "Created configs: $output_file"
}

# Configuration - EDIT THESE VALUES
BENCHMARK_ID="benchmark-$(date +%s)"
# INSTANCE_GROUP="princer-test"
INSTANCE_GROUP="princer-c4-192-us-west4-a-mg"
TEST_CSV="sample-tests.csv"
FIO_JOB_FILE="jobfile.fio"
BUCKET="princer-zonal-us-west4-a"
ARTIFACTS_BUCKET="princer-working-dirs"
ZONE="us-west4-a"
PROJECT="gcs-tess"
ITERATIONS=1

# For single-config mode (leave empty for multi-config):
GCSFUSE_COMMIT="master"
GCSFUSE_MOUNT_ARGS="--stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 --metadata-cache-ttl-secs=2000 --enable-kernel-reader=false"

# For multi-config mode (set to configs.csv file path):
CONFIGS_CSV="configs.csv"  # Set to file path to enable multi-config mode, e.g., "configs.csv"
SEPARATE_CONFIGS=false  # Set to true to generate separate CSV per config

# GCSFUSE_MOUNT_ARGS="--stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 --metadata-cache-ttl-secs=2000"
# GCSFUSE_MOUNT_ARGS="--implicit-dirs --stat-cache-max-size-mb=-1 --stat-cache-ttl=2h --max-read-ahead-kb=8192 --max-background=600 --congestion-threshold=600"

# Advanced options
POLL_INTERVAL=30
TIMEOUT=400

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

if [ -n "$CONFIGS_CSV" ]; then
    echo "Mode: Multi-Config"
    echo "Configs CSV: $CONFIGS_CSV"
    echo "Separate Configs: $SEPARATE_CONFIGS"
else
    echo "Mode: Single-Config"
    echo "GCSFuse Commit: $GCSFUSE_COMMIT"
    echo "Mount Args: $GCSFUSE_MOUNT_ARGS"
fi

echo "=========================================="
echo ""

# Always generate FIO job file with latest content
echo "Generating FIO job file: $FIO_JOB_FILE"
create_fio_config "$FIO_JOB_FILE"

# Always generate test CSV with latest content
echo "Generating test cases: $TEST_CSV"
create_test_cases "$TEST_CSV"

# Verify test CSV was created correctly
TEST_COUNT=$(tail -n +2 "$TEST_CSV" | wc -l)
echo "  Created $TEST_COUNT test cases"

# Always generate configs if in multi-config mode
if [ -n "$CONFIGS_CSV" ]; then
    echo "Generating configs: $CONFIGS_CSV"
    create_configs "$CONFIGS_CSV"
    CONFIG_COUNT=$(tail -n +2 "$CONFIGS_CSV" | wc -l)
    echo "  Created $CONFIG_COUNT configs"
    echo "  Total matrix size: $((TEST_COUNT * CONFIG_COUNT)) tests"
fi

# Create results directory
mkdir -p results

# Build orchestrator command
ORCHESTRATOR_CMD="python3 orchestrator.py \
    --benchmark-id $BENCHMARK_ID \
    --instance-group $INSTANCE_GROUP \
    --zone $ZONE \
    --project $PROJECT \
    --artifacts-bucket $ARTIFACTS_BUCKET \
    --test-csv $TEST_CSV \
    --fio-job-file $FIO_JOB_FILE \
    --bucket $BUCKET \
    --iterations $ITERATIONS \
    --poll-interval $POLL_INTERVAL \
    --timeout $TIMEOUT"

# Add config-specific parameters
if [ -n "$CONFIGS_CSV" ]; then
    ORCHESTRATOR_CMD="$ORCHESTRATOR_CMD --configs-csv $CONFIGS_CSV"
    if [ "$SEPARATE_CONFIGS" = true ]; then
        ORCHESTRATOR_CMD="$ORCHESTRATOR_CMD --separate-configs"
    fi
else
    ORCHESTRATOR_CMD="$ORCHESTRATOR_CMD --gcsfuse-commit $GCSFUSE_COMMIT --gcsfuse-mount-args=\"$GCSFUSE_MOUNT_ARGS\""
fi

# Run orchestrator
echo ""
eval $ORCHESTRATOR_CMD

echo ""
echo "=========================================="
echo "Benchmark Complete!"
echo "Benchmark ID: $BENCHMARK_ID"
echo "Results: results/${BENCHMARK_ID}_report.txt"
echo "=========================================="

