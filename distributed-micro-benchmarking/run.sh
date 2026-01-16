#!/bin/bash
#
# Distributed Micro-Benchmarking Run Script
#

set -e

# Configuration - EDIT THESE VALUES
BENCHMARK_ID="benchmark-$(date +%s)"
# INSTANCE_GROUP="princer-test"
INSTANCE_GROUP="princer-c4-192-us-west4-a-mg"
# TEST_CSV="test-suites/base/large_sequential.csv"
# TEST_CSV="test-suites/tune_kernel_settings/minimal_test.csv"
TEST_CSV="test-suites/sequential-read-tests/full_test_cases.csv"
# TEST_CSV="test-suites/random-read-tests/test-cases.csv"
# FIO_JOB_FILE="test-suites/base/jobfile.fio"
# FIO_JOB_FILE="test-suites/tune_kernel_settings/jobfile.fio"
FIO_JOB_FILE="test-suites/sequential-read-tests/jobfile-libaio.fio"
# FIO_JOB_FILE="test-suites/random-read-tests/jobfile-libaio.fio"
BUCKET="princer-zonal-us-west4-a"
ARTIFACTS_BUCKET="princer-working-dirs"
ZONE="us-west4-a"
PROJECT="gcs-tess"
ITERATIONS=3

# For single-config mode (leave empty for multi-config):
GCSFUSE_COMMIT="master"
GCSFUSE_MOUNT_ARGS="--stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 --metadata-cache-ttl-secs=2000 --enable-kernel-reader=false"

# For multi-config mode (set to configs.csv file path):
# CONFIGS_CSV="test-suites/base/configs.csv"  # Set to file path to enable multi-config mode, e.g., "test-suites/base/configs.csv"
# CONFIGS_CSV="test-suites/tune_kernel_settings/minmal_config.csv"
CONFIGS_CSV="test-suites/sequential-read-tests/configs.csv"
# CONFIGS_CSV="test-suites/random-read-tests/configs.csv"

SEPARATE_CONFIGS=false  # Set to true to generate separate CSV per config

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

# Verify required files exist
if [ ! -f "$FIO_JOB_FILE" ]; then
    echo "ERROR: FIO job file not found: $FIO_JOB_FILE"
    exit 1
fi
echo "Using FIO job file: $FIO_JOB_FILE"

if [ ! -f "$TEST_CSV" ]; then
    echo "ERROR: Test CSV file not found: $TEST_CSV"
    exit 1
fi
echo "Using test cases: $TEST_CSV"
TEST_COUNT=$(tail -n +2 "$TEST_CSV" | wc -l)
echo "  Found $TEST_COUNT test cases"

if [ -n "$CONFIGS_CSV" ]; then
    if [ ! -f "$CONFIGS_CSV" ]; then
        echo "ERROR: Configs CSV file not found: $CONFIGS_CSV"
        exit 1
    fi
    echo "Using configs: $CONFIGS_CSV"
    CONFIG_COUNT=$(tail -n +2 "$CONFIGS_CSV" | wc -l)
    echo "  Found $CONFIG_COUNT configs"
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

