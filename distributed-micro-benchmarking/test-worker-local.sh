#!/bin/bash
#
# Local Worker Test Script
# Run worker.sh directly on a VM for testing/debugging
#
# Usage:
#   1. From orchestrator machine (setup and SSH to VM):
#      ./test-worker-local.sh <vm-name>
#
#   2. On the worker VM (run tests):
#      ./test-worker-local.sh
#

set -e

# Check if VM name is provided (setup mode)
if [ -n "$1" ]; then
    VM_NAME="$1"
    ZONE="us-west4-a"
    PROJECT="gcs-tess"
    
    echo "=========================================="
    echo "Setup Mode: Copying files to VM"
    echo "=========================================="
    echo "VM: $VM_NAME"
    echo "Zone: $ZONE"
    echo "Project: $PROJECT"
    echo ""
    
    # Create resources directory on VM
    echo "Creating resources directory on VM..."
    gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --internal-ip --command="mkdir -p ~/resources"
    
    # Copy files to VM
    echo "Copying test-worker-local.sh..."
    gcloud compute scp test-worker-local.sh "$VM_NAME":~/ --zone="$ZONE" --project="$PROJECT" --internal-ip
    
    echo "Copying worker.sh..."
    gcloud compute scp resources/worker.sh "$VM_NAME":~/resources/ --zone="$ZONE" --project="$PROJECT" --internal-ip
    
    echo ""
    echo "✓ Files copied successfully"
    echo ""
    echo "Next steps:"
    echo "  1. Edit the BENCHMARK_ID in the script (see below)"
    echo "  2. SSH to the VM: gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT --internal-ip"
    echo "  3. Run: ./test-worker-local.sh"
    echo ""
    echo "=========================================="
    exit 0
fi

# ============================================
# CONFIGURATION - EDIT THESE VALUES
# ============================================

# These should match your orchestrator run
BENCHMARK_ID="benchmark-1767513046"  # Replace with actual benchmark ID
ARTIFACTS_BUCKET="princer-working-dirs"  # Replace with your artifacts bucket

# ============================================
# Script starts here
# ============================================

echo "=========================================="
echo "Local Worker Test"
echo "=========================================="
echo "Benchmark ID: $BENCHMARK_ID"
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo ""

# Check if worker script exists
if [ ! -f "resources/worker.sh" ]; then
    echo "ERROR: resources/worker.sh not found"
    echo "Please run this script from the distributed-micro-benchmarking directory"
    exit 1
fi

# Verify GCS access
echo "Verifying GCS access..."
if ! gcloud storage ls "gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/" &>/dev/null; then
    echo "ERROR: Cannot access gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/"
    echo "Make sure:"
    echo "  1. The benchmark ID is correct"
    echo "  2. The orchestrator has uploaded files to GCS"
    echo "  3. This VM has read access to the bucket"
    exit 1
fi

echo "✓ GCS access verified"
echo ""

# Run worker script
echo "Starting worker script..."
echo "=========================================="
echo ""

bash resources/worker.sh "$BENCHMARK_ID" "$ARTIFACTS_BUCKET"

echo ""
echo "=========================================="
echo "Worker test completed"
echo "=========================================="
