#!/bin/bash
# A simple script to run the GKE NPI benchmark Jobs sequentially
# to avoid resource contention and interference.

set -e

# Make sure we're running from the right directory
if [ ! -d "gke_pod_specs" ]; then
  echo "Error: Directory gke_pod_specs not found. Run this from gcsfuse-tools/npi/"
  exit 1
fi

echo "Starting sequential benchmark execution..."

for spec in gke_pod_specs/*.yaml; do
  echo "=========================================================="
  echo "Applying benchmark Job: $spec"
  kubectl apply -f "$spec"
  
  # Extract the job name from the YAML (assuming it's on the first 'name:' line)
  JOB_NAME=$(grep "name:" "$spec" | head -1 | awk '{print $2}')
  
  # Wait for the job's pod to be created before we can tail logs
  echo "Waiting for Job pod to be scheduled..."
  sleep 5
  
  # Tail logs until the container exits
  echo "Tailing logs for Job $JOB_NAME..."
  kubectl logs -f "job/$JOB_NAME" || echo "Note: kubectl logs exited."

  # Wait to ensure completion status is registered
  kubectl wait --for=condition=complete "job/$JOB_NAME" --timeout=600s || echo "Job didn't complete or timed out."

  echo "Benchmark completed. Cleaning up $JOB_NAME..."
  kubectl delete -f "$spec"
  
  echo "Done with $spec"
  echo "=========================================================="
  sleep 5
done

echo "All benchmarks completed sequentially."
