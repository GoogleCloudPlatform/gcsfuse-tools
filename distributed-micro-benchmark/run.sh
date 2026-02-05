#!/bin/bash
#
# Distributed Micro-Benchmarking Run Script
#
# Usage: run.sh [fio_job_file] [test_csv] [config_csv]
#
# Arguments:
#   fio_job_file  - Path to FIO job file (optional, default: test_suites/base/fio-job-default.fio)
#   test_csv      - Path to test cases CSV (optional, default: test_suites/base/test-cases-large-sequential.csv)
#   config_csv    - Path to configs CSV (optional, default: test_suites/base/mount-configs.csv)
#
# Examples:
#   ./run.sh
#   ./run.sh test_suites/base/fio-job-default.fio
#   ./run.sh test_suites/base/fio-job-default.fio test_suites/base/test-cases-large-sequential.csv
#   ./run.sh test_suites/base/fio-job-default.fio test_suites/base/test-cases-large-sequential.csv test_suites/base/mount-configs.csv
#

set -e

# Update these defaults in your run.sh to match the test files
FIO_JOB_FILE="${1:-test_suites/base/fio-job-default.fio}"
TEST_CSV="${2:-test_suites/base/test-cases-sample.csv}"
CONFIGS_CSV="${3:-test_suites/base/mount-configs.csv}"

# Configuration - EDIT THESE VALUES
BENCHMARK_ID="benchmark-$(date +%s)"
# INSTANCE_GROUP="princer-test"
INSTANCE_GROUP="princer-c4-192-us-west4-a-mg"
BUCKET="princer-zonal-us-west4-a"
ARTIFACTS_BUCKET="princer-working-dirs"
ZONE="us-west4-a"
PROJECT="gcs-tess"
ITERATIONS=1

# For single-config mode (leave empty for multi-config):
GCSFUSE_COMMIT="master"
GCSFUSE_MOUNT_ARGS="--stat-cache-max-size-mb=-1 --type-cache-max-size-mb=-1 --metadata-cache-ttl-secs=2000 --enable-kernel-reader=false"

SEPARATE_CONFIGS=false  # Set to true to generate separate CSV per config

# Advanced options
POLL_INTERVAL=30
TIMEOUT=14400

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

# 7. Upload to BigQuery (Optional)
# Determines script location to allow running from anywhere
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
BQ_SCRIPT="$SCRIPT_DIR/helpers/upload_to_bq.py"
RESULTS_DIR="results/${BENCHMARK_ID}"

# Check if BQ script exists and results were generated
if [ -f "$BQ_SCRIPT" ] && [ -d "$RESULTS_DIR" ]; then
    echo ""
    echo "=========================================="
    echo "Uploading results to BigQuery..."
    
    # # Check if running in Kokoro (env var usually set in CI)
    # IS_KOKORO_FLAG=""
    # if [ "${KOKORO_BUILD_ID:-}" != "" ]; then
    #     IS_KOKORO_FLAG="--is-kokoro"
    # fi

    # Execute upload
    python3 "$BQ_SCRIPT" \
        --results-dir "$RESULTS_DIR" \
        --project-id "gcs-fuse-test-ml" \
        $IS_KOKORO_FLAG
else
    echo "Skipping BigQuery upload (Script or Results not found)"
fi

echo ""
echo "=========================================="
