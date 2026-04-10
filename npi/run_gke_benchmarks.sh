#!/bin/bash
# A simple script to run the GKE NPI benchmarks sequentially
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
  echo "Applying benchmark: $spec"
  kubectl apply -f "$spec"
  
  # Extract the pod name from the YAML (assuming it's on the first 'name:' line)
  POD_NAME=$(grep "name:" "$spec" | head -1 | awk '{print $2}')
  
  echo "Waiting for pod $POD_NAME to initialize..."
  
  # Wait for the pod to be created and transition out of Pending
  while true; do
    PHASE=$(kubectl get pod "$POD_NAME" -o 'jsonpath={.status.phase}' 2>/dev/null || echo "Unknown")
    if [ "$PHASE" != "Pending" ] && [ "$PHASE" != "Unknown" ]; then
      break
    fi
    sleep 5
  done

  echo "Pod $POD_NAME is now in phase: $PHASE"
  echo "Tailing logs... (this will block until the benchmark completes)"
  
  # Tail logs until the container exits
  kubectl logs -f "$POD_NAME" || echo "Note: kubectl logs exited."

  echo "Benchmark completed. Cleaning up $POD_NAME..."
  kubectl delete -f "$spec"
  
  echo "Done with $spec"
  echo "=========================================================="
  sleep 5
done

echo "All benchmarks completed sequentially."
