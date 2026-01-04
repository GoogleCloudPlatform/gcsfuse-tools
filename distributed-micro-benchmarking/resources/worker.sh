#!/bin/bash
#
# Distributed Micro-Benchmarking Worker
# Runs on each VM to execute assigned tests
#
# Usage: worker.sh <benchmark_id> <artifacts_bucket>
#

set -e

BENCHMARK_ID="$1"
ARTIFACTS_BUCKET="$2"

if [ -z "$BENCHMARK_ID" ] || [ -z "$ARTIFACTS_BUCKET" ]; then
    echo "Usage: $0 <benchmark_id> <artifacts_bucket>"
    exit 1
fi

# Get VM name
VM_NAME=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name)
echo "VM: $VM_NAME"
echo "Benchmark ID: $BENCHMARK_ID"

# Setup workspace
WORKSPACE="/tmp/benchmark-${BENCHMARK_ID}"
rm -rf "$WORKSPACE"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# Error handler - mark as failed on any error
trap 'handle_error $?' ERR EXIT

handle_error() {
    local exit_code=$1
    
    # Only handle non-zero exit codes and avoid double-handling
    if [ $exit_code -ne 0 ] && [ ! -f "$WORKSPACE/.error_handled" ]; then
        touch "$WORKSPACE/.error_handled"
        
        echo "ERROR: Script failed with exit code $exit_code"
        
        # Upload logs if available
        if [ -n "$LOG_BASE" ] && [ -f "$LOG_FILE" ]; then
            gcloud storage cp "$LOG_FILE" "${LOG_BASE}/worker.log" 2>/dev/null || true
        fi
        
        # Update manifest to failed status if it exists
        if [ -f "$WORKSPACE/manifest.json" ]; then
            END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
            jq ".status = \"failed\" | .end_time = \"$END_TIME\" | .error_code = $exit_code" manifest.json > manifest_tmp.json
            mv manifest_tmp.json manifest.json
            
            # Upload failed manifest
            if [ -n "$RESULT_BASE" ]; then
                gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json" || true
            fi
        fi
        
        echo "✗ Worker failed"
    fi
}

# Download job specification
JOB_FILE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/jobs/${VM_NAME}.json"
echo "Downloading job: $JOB_FILE"
gcloud storage cp "$JOB_FILE" job.json

# Download test cases
TEST_CASES_FILE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/test-cases.csv"
echo "Downloading test cases: $TEST_CASES_FILE"
gcloud storage cp "$TEST_CASES_FILE" test-cases.csv

# Download FIO job template
FIO_TEMPLATE_FILE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/jobfile.fio"
echo "Downloading FIO job template: $FIO_TEMPLATE_FILE"
gcloud storage cp "$FIO_TEMPLATE_FILE" jobfile.fio

# Download config to get GCSFuse commit
CONFIG_FILE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/config.json"
echo "Downloading config: $CONFIG_FILE"
gcloud storage cp "$CONFIG_FILE" config.json

# Parse job spec
BUCKET=$(jq -r '.bucket' job.json)
ITERATIONS=$(jq -r '.iterations' job.json)
TEST_IDS=$(jq -r '.test_ids | join(" ")' job.json)
GCSFUSE_COMMIT=$(jq -r '.gcsfuse_commit // "master"' config.json)
GCSFUSE_MOUNT_ARGS=$(jq -r '.gcsfuse_mount_args // "--implicit-dirs"' config.json)

echo "Test bucket: $BUCKET"
echo "Iterations: $ITERATIONS"
echo "Test IDs: $TEST_IDS"
echo "GCSFuse commit: $GCSFUSE_COMMIT"
echo "GCSFuse mount args: $GCSFUSE_MOUNT_ARGS"

# Install dependencies if not present
echo "Setting up dependencies..."

# Install Git if not present
if ! command -v git &> /dev/null; then
    echo "  Installing Git..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq git
fi

# Install Go if not present
if ! command -v go &> /dev/null; then
    echo "  Installing Go..."
    cd /tmp
    wget -q https://go.dev/dl/go1.22.0.linux-amd64.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf go1.22.0.linux-amd64.tar.gz
    export PATH=$PATH:/usr/local/go/bin
    cd "$WORKSPACE"
fi

# Install FIO if not present
if ! command -v fio &> /dev/null; then
    echo "  Installing FIO..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq fio
fi

# Install bc for calculations
if ! command -v bc &> /dev/null; then
    echo "  Installing bc..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq bc
fi

# Build GCSFuse
echo "Building GCSFuse from commit: $GCSFUSE_COMMIT"
GCSFUSE_DIR="$WORKSPACE/gcsfuse"
git clone https://github.com/GoogleCloudPlatform/gcsfuse.git "$GCSFUSE_DIR"
cd "$GCSFUSE_DIR"
git checkout "$GCSFUSE_COMMIT"
go build -o gcsfuse
GCSFUSE_BIN="$GCSFUSE_DIR/gcsfuse"
cd "$WORKSPACE"

echo "GCSFuse binary: $GCSFUSE_BIN"

# Setup mount directory
MOUNT_DIR="$WORKSPACE/mnt"
mkdir -p "$MOUNT_DIR"

# Create manifest
START_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")

cat > manifest.json <<EOF
{
  "vm_name": "$VM_NAME",
  "status": "running",
  "start_time": "$START_TIME",
  "tests": []
}
EOF

# Upload initial manifest
RESULT_BASE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/results/${VM_NAME}"
LOG_BASE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/logs/${VM_NAME}"
LOG_FILE="/tmp/worker_${BENCHMARK_ID}.log"
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

# Run each assigned test
for TEST_ID in $TEST_IDS; do
    echo "=========================================="
    echo "Running Test ID: $TEST_ID"
    
    # Extract test parameters from CSV
    TEST_LINE=$(sed -n "$((TEST_ID + 1))p" test-cases.csv)
    BS=$(echo "$TEST_LINE" | cut -d',' -f1)
    FILE_SIZE=$(echo "$TEST_LINE" | cut -d',' -f2)
    IO_DEPTH=$(echo "$TEST_LINE" | cut -d',' -f3)
    IO_TYPE=$(echo "$TEST_LINE" | cut -d',' -f4)
    THREADS=$(echo "$TEST_LINE" | cut -d',' -f5)
    NRFILES=$(echo "$TEST_LINE" | cut -d',' -f6)
    
    echo "Parameters: bs=$BS, file_size=$FILE_SIZE, io_depth=$IO_DEPTH, io_type=$IO_TYPE, threads=$THREADS, nrfiles=$NRFILES"
    
    TEST_DIR="test-${TEST_ID}"
    mkdir -p "$TEST_DIR"
    
    # Create FIO job file from template with variable substitution
    FIO_JOB="$TEST_DIR/job.fio"
    TEST_DATA_DIR="$MOUNT_DIR/$FILE_SIZE"
    export BS FILE_SIZE IO_DEPTH IO_TYPE THREADS NRFILES TEST_DATA_DIR
    envsubst '$BS $FILE_SIZE $IO_DEPTH $IO_TYPE $THREADS $NRFILES $TEST_DATA_DIR' < jobfile.fio > "$FIO_JOB"
    
    # Initialize monitoring file
    MONITOR_FILE="$TEST_DIR/monitor.log"
    echo "timestamp,cpu_percent,mem_rss_mb,mem_vsz_mb" > "$MONITOR_FILE"
    
    # Run FIO iterations with mount/unmount for each
    for ((i=1; i<=ITERATIONS; i++)); do
        echo "  Iteration $i/$ITERATIONS"
        
        # Mount GCSFuse
        echo "    Mounting GCSFuse..."
        $GCSFUSE_BIN $GCSFUSE_MOUNT_ARGS "$BUCKET" "$MOUNT_DIR"
        
        # Get GCSFuse PID for monitoring
        GCSFUSE_PID=$(pgrep -f "gcsfuse.*$BUCKET" | head -1)
        echo "    GCSFuse PID: $GCSFUSE_PID"
        
        # Start memory and CPU monitoring in background
        MONITOR_STOP_FLAG="$TEST_DIR/monitor_stop_${i}"
        rm -f "$MONITOR_STOP_FLAG"
        
        (
            while [ ! -f "$MONITOR_STOP_FLAG" ]; do
                if [ -d "/proc/$GCSFUSE_PID" ]; then
                    TIMESTAMP=$(date +%s)
                    # Get CPU and memory stats from /proc
                    CPU_PERCENT=$(ps -p $GCSFUSE_PID -o %cpu= 2>/dev/null || echo "0")
                    MEM_RSS_KB=$(ps -p $GCSFUSE_PID -o rss= 2>/dev/null || echo "0")
                    MEM_VSZ_KB=$(ps -p $GCSFUSE_PID -o vsz= 2>/dev/null || echo "0")
                    MEM_RSS_MB=$(echo "scale=2; $MEM_RSS_KB / 1024" | bc)
                    MEM_VSZ_MB=$(echo "scale=2; $MEM_VSZ_KB / 1024" | bc)
                    echo "$TIMESTAMP,$CPU_PERCENT,$MEM_RSS_MB,$MEM_VSZ_MB" >> "$MONITOR_FILE"
                fi
                sleep 2
            done
        ) &
        MONITOR_PID=$!
        
        # Create subdirectory for this file size
        mkdir -p "$TEST_DATA_DIR"
        
        # Run FIO
        OUTPUT_FILE="${TEST_DIR}/fio_output_${i}.json"
        fio "$FIO_JOB" --output-format=json --output="$OUTPUT_FILE"
        
        # Stop monitoring
        touch "$MONITOR_STOP_FLAG"
        sleep 1
        kill $MONITOR_PID 2>/dev/null || true
        wait $MONITOR_PID 2>/dev/null || true
        
        # Unmount GCSFuse
        echo "    Unmounting GCSFuse..."
        fusermount -u "$MOUNT_DIR" 2>/dev/null || umount "$MOUNT_DIR" 2>/dev/null || true
        
        # Wait a moment before next iteration
        if [ $i -lt $ITERATIONS ]; then
            sleep 2
        fi
    done
    
    # Calculate and report average CPU usage across all iterations
    if [ -f "$MONITOR_FILE" ]; then
        AVG_CPU=$(awk -F',' 'NR>1 {sum+=$2; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
        MAX_CPU=$(awk -F',' 'NR>1 {if($2>max) max=$2} END {printf "%.2f", max+0}' "$MONITOR_FILE")
        AVG_MEM_RSS=$(awk -F',' 'NR>1 {sum+=$3; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
        MAX_MEM_RSS=$(awk -F',' 'NR>1 {if($3>max) max=$3} END {printf "%.2f", max+0}' "$MONITOR_FILE")
        echo "  Resource Usage - Avg CPU: ${AVG_CPU}%, Peak CPU: ${MAX_CPU}%, Avg Memory: ${AVG_MEM_RSS}MB, Peak Memory: ${MAX_MEM_RSS}MB"
    fi
    
    # Upload test results
    gcloud storage cp -r "$TEST_DIR" "${RESULT_BASE}/"
    
    # Update manifest with resource usage
    TEST_PARAMS="{\"bs\":\"$BS\",\"file_size\":\"$FILE_SIZE\",\"io_depth\":\"$IO_DEPTH\",\"io_type\":\"$IO_TYPE\",\"threads\":\"$THREADS\",\"nrfiles\":\"$NRFILES\",\"avg_cpu\":\"$AVG_CPU\",\"peak_cpu\":\"$MAX_CPU\",\"avg_mem_mb\":\"$AVG_MEM_RSS\",\"peak_mem_mb\":\"$MAX_MEM_RSS\"}"
    jq ".tests += [{\"test_id\":$TEST_ID,\"status\":\"success\",\"params\":$TEST_PARAMS}]" manifest.json > manifest_tmp.json
    mv manifest_tmp.json manifest.json
    
    echo "  ✓ Test $TEST_ID completed"
done

# Finalize manifest
END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
TOTAL_TESTS=$(echo "$TEST_IDS" | wc -w)

jq ".status = \"completed\" | .end_time = \"$END_TIME\" | .total_tests = $TOTAL_TESTS" manifest.json > manifest_tmp.json
mv manifest_tmp.json manifest.json

# Upload final manifest
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

# Disable error trap for successful completion
trap - ERR EXIT

echo "=========================================="
echo "✓ All tests completed successfully"
echo "Results uploaded to: $RESULT_BASE"
