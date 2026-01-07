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
1m,1g,2,randread,1,20
1m,1m,2,randread,1,200
1m,2g,2,randread,1,10
1m,4m,2,randread,1,150
1m,5g,2,randread,1,10
1m,8m,2,randread,1,120
1m,10g,2,randread,1,4
1m,16m,2,randread,1,120
1m,32m,2,randread,1,100
1m,64m,2,randread,1,100
64k,64k,2,randread,1,400
256k,256k,2,randread,1,400
1m,1g,2,randread,48,20
1m,1m,2,randread,48,200
1m,2g,2,randread,48,10
1m,4m,2,randread,48,150
1m,5g,2,randread,48,10
1m,8m,2,randread,48,120
1m,10g,2,randread,48,4
1m,16m,2,randread,48,120
1m,32m,2,randread,48,100
1m,64m,2,randread,48,100
64k,64k,2,randread,48,400
256k,256k,2,randread,48,400
1m,1g,2,randread,96,20
1m,1m,2,randread,96,200
1m,2g,2,randread,96,10
1m,4m,2,randread,96,150
1m,5g,2,randread,96,10
1m,8m,2,randread,96,120
1m,10g,2,randread,96,4
1m,16m,2,randread,96,120
1m,32m,2,randread,96,100
1m,64m,2,randread,96,100
64k,64k,2,randread,96,400
256k,256k,2,randread,96,400
1m,1g,2,read,1,20
1m,1m,2,read,1,200
1m,2g,2,read,1,10
1m,4m,2,read,1,150
1m,5g,2,read,1,10
1m,8m,2,read,1,120
1m,10g,2,read,1,4
1m,16m,2,read,1,120
1m,32m,2,read,1,100
1m,64m,2,read,1,100
64k,64k,2,read,1,400
256k,256k,2,read,1,400
1m,1g,2,read,48,20
1m,1m,2,read,48,200
1m,2g,2,read,48,10
1m,4m,2,read,48,150
1m,5g,2,read,48,10
1m,8m,2,read,48,120
1m,10g,2,read,48,4
1m,16m,2,read,48,120
1m,32m,2,read,48,100
1m,64m,2,read,48,100
64k,64k,2,read,48,400
256k,256k,2,read,48,400
1m,1g,2,read,96,20
1m,1m,2,read,96,200
1m,2g,2,read,96,10
1m,4m,2,read,96,150
1m,5g,2,read,96,10
1m,8m,2,read,96,120
1m,10g,2,read,96,4
1m,16m,2,read,96,120
1m,32m,2,read,96,100
1m,64m,2,read,96,100
64k,64k,2,read,96,400
256k,256k,2,read,96,400
TEST_CSV_EOF
    echo "Created test cases: $output_file"
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
ITERATIONS=3
# GCSFUSE_COMMIT="master"
GCSFUSE_MOUNT_ARGS="--implicit-dirs --stat-cache-max-size-mb=-1 --stat-cache-ttl=2h"
GCSFUSE_COMMIT="default_fuse_settings"
# GCSFUSE_MOUNT_ARGS="--implicit-dirs --stat-cache-max-size-mb=-1 --stat-cache-ttl=2h --max-background=600 --congestion-threshold=600"
# GCSFUSE_MOUNT_ARGS="--implicit-dirs --stat-cache-max-size-mb=-1 --stat-cache-ttl=2h --max-read-ahead-kb=8192 --max-background=600 --congestion-threshold=600"

# Advanced options
POLL_INTERVAL=30
TIMEOUT=72000

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

# Always generate FIO job file with latest content
echo "Generating FIO job file: $FIO_JOB_FILE"
create_fio_config "$FIO_JOB_FILE"

# Always generate test CSV with latest content
echo "Generating test cases: $TEST_CSV"
create_test_cases "$TEST_CSV"

# Create results directory
mkdir -p results

# Run orchestrator
echo ""
python3 orchestrator.py \
    --benchmark-id "$BENCHMARK_ID" \
    --instance-group "$INSTANCE_GROUP" \
    --zone "$ZONE" \
    --project "$PROJECT" \
    --artifacts-bucket "$ARTIFACTS_BUCKET" \
    --test-csv "$TEST_CSV" \
    --fio-job-file "$FIO_JOB_FILE" \
    --bucket "$BUCKET" \
    --iterations "$ITERATIONS" \
    --gcsfuse-commit "$GCSFUSE_COMMIT" \
    --gcsfuse-mount-args="$GCSFUSE_MOUNT_ARGS" \
    --poll-interval "$POLL_INTERVAL" \
    --timeout "$TIMEOUT"

echo ""
echo "=========================================="
echo "Benchmark Complete!"
echo "Benchmark ID: $BENCHMARK_ID"
echo "Results: results/${BENCHMARK_ID}_report.txt"
echo "=========================================="

