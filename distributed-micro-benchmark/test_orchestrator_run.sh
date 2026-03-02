#!/bin/bash
set -e

# Install dependencies
sudo apt-get install python3-tabulate

# --- 0. Set Working Directory ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# --- 1. Configuration ---
PROJECT_ID="gcs-fuse-test"
ZONE="us-west8-a"
REGION="us-west8"
BUCKET_NAME="orchestrator-test-bucket-predefined"
BENCHMARK_ID="orch-e2e-$(date +%s)"
INSTANCE_GROUP="instance-group-1"
TEMPLATE_SINGLE="orch-test-single-predefined"
TEMPLATE_MULTI="orch-test-multi-predefined"

echo "--- 2. Running Orchestrator ---"
python3 orchestrator.py \
    --benchmark-id "${BENCHMARK_ID}" \
    --executor-vm "${INSTANCE_GROUP}" \
    --zone "${ZONE}" \
    --project "${PROJECT_ID}" \
    --artifacts-bucket "${BUCKET_NAME}" \
    --test-csv "test_suites/test_orchestrator/test_cases_sample.csv" \
    --configs-csv "test_suites/test_orchestrator/test_mount_configs.csv" \
    --fio-job-file "test_suites/test_orchestrator/test_read.fio" \
    --test-data-bucket "${BUCKET_NAME}" \
    --iterations 1 \
    --poll-interval 20 \
    --timeout 1000 \
    --single-thread-vm-type "${TEMPLATE_SINGLE}" \
    --multi-thread-vm-type "${TEMPLATE_MULTI}"

echo "Benchmark finished successfully. Cleaning up resources..."
