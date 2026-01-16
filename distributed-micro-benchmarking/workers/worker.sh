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
echo "Artifacts Bucket: $ARTIFACTS_BUCKET"
echo "Start time: $(date)"

# Set up paths early for error handling
RESULT_BASE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/results/${VM_NAME}"
LOG_BASE="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/logs/${VM_NAME}"
LOG_FILE="/tmp/worker_${BENCHMARK_ID}.log"

# Redirect all output to log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=========================================="
echo "Worker Starting"
echo "=========================================="

# Setup workspace
WORKSPACE="/tmp/benchmark-${BENCHMARK_ID}"
rm -rf "$WORKSPACE"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

##############################################################################
# SECTION 1: SETUP
#
# This section defines all functions and performs initial setup:
# - Error handling and cleanup functions (cleanup_gcsfuse, handle_error)
# - Download job specs, test cases, FIO templates, and config from GCS
# - Install dependencies (git, go, fio, bc) if not present
# - Pre-flight checks for required commands
# - Build GCSFuse from specified commit (build_gcsfuse_for_commit)
# - Parse test parameters from CSV (parse_test_params)
# - Resource monitoring functions (start_monitoring, stop_monitoring, calculate_metrics)
# - Test execution functions (run_test_iterations, execute_test)
##############################################################################

# Cleanup function - unmount GCSFuse and kill processes
cleanup_gcsfuse() {
    echo "Cleaning up GCSFuse mounts and processes..." >&2
    
    # Unmount if mounted
    if [ -n "${MOUNT_DIR:-}" ] && mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
        echo "  Unmounting $MOUNT_DIR..." >&2
        fusermount -u "$MOUNT_DIR" 2>/dev/null || umount -f "$MOUNT_DIR" 2>/dev/null || true
        sleep 1
    fi
    
    # Kill any GCSFuse processes
    local GCSFUSE_PIDS=$(pgrep -f "gcsfuse.*${MOUNT_DIR:-mnt}" || true)
    if [ -n "$GCSFUSE_PIDS" ]; then
        echo "  Killing GCSFuse processes: $GCSFUSE_PIDS" >&2
        kill -9 $GCSFUSE_PIDS 2>/dev/null || true
    fi
}

# Error handler - mark as failed on any error
trap 'handle_error $?' ERR EXIT

handle_error() {
    local exit_code=$1
    
    # Only handle non-zero exit codes and avoid double-handling
    if [ $exit_code -ne 0 ] && [ ! -f "$WORKSPACE/.error_handled" ]; then
        touch "$WORKSPACE/.error_handled"
        
        echo "ERROR: Script failed with exit code $exit_code"
        
        # Cleanup GCSFuse mounts and processes
        cleanup_gcsfuse
        
        # Stop cancellation monitor if running
        if [ -n "$CANCEL_CHECK_PID" ]; then
            kill $CANCEL_CHECK_PID 2>/dev/null || true
        fi
        
        # Check if cancellation caused the failure
        CANCEL_FLAG="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/cancel"
        CANCELLED=false
        if gcloud storage ls "$CANCEL_FLAG" > /dev/null 2>&1; then
            CANCELLED=true
            STATUS="cancelled"
            echo "Job was cancelled"
        else
            STATUS="failed"
        fi
        
        # Upload logs if available
        if [ -n "$LOG_BASE" ] && [ -f "$LOG_FILE" ]; then
            gcloud storage cp "$LOG_FILE" "${LOG_BASE}/worker.log" 2>/dev/null || true
        fi
        
        # Update manifest to failed/cancelled status if it exists
        if [ -f "$WORKSPACE/manifest.json" ]; then
            END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
            jq ".status = \"$STATUS\" | .end_time = \"$END_TIME\" | .error_code = $exit_code" manifest.json > manifest_tmp.json
            mv manifest_tmp.json manifest.json
            
            # Upload manifest
            if [ -n "$RESULT_BASE" ]; then
                gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json" || true
            fi
        fi
        
        if $CANCELLED; then
            echo "✓ Job cancelled gracefully"
        else
            echo "✗ Worker failed"
        fi
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

# Parse job spec and config
MODE=$(jq -r '.mode // "single-config"' config.json)
BUCKET=$(jq -r '.bucket' job.json)
ITERATIONS=$(jq -r '.iterations' job.json)

echo "Mode: $MODE"
echo "Test bucket: $BUCKET"
echo "Iterations: $ITERATIONS"

# In single-config mode, read commit/mount_args from config
# In multi-config mode, each test entry has its own commit/mount_args
if [ "$MODE" = "single-config" ]; then
    GCSFUSE_COMMIT=$(jq -r '.gcsfuse_commit // "master"' config.json)
    GCSFUSE_MOUNT_ARGS=$(jq -r '.gcsfuse_mount_args // ""' config.json)
    echo "GCSFuse commit: $GCSFUSE_COMMIT"
    echo "GCSFuse mount args: $GCSFUSE_MOUNT_ARGS"
fi

# Pre-flight checks
echo "Running pre-flight checks..."

# Check required commands
REQUIRED_CMDS="gcloud jq"
for cmd in $REQUIRED_CMDS; do
    if ! command -v $cmd &> /dev/null; then
        echo "ERROR: Required command '$cmd' not found"
        exit 1
    fi
done

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

# Build GCSFuse for single-config mode
# In multi-config mode, GCSFuse will be built per-config as needed
if [ "$MODE" = "single-config" ]; then
    echo "Building GCSFuse from commit: $GCSFUSE_COMMIT"
    GCSFUSE_DIR="$WORKSPACE/gcsfuse"
    
    if ! git clone https://github.com/GoogleCloudPlatform/gcsfuse.git "$GCSFUSE_DIR" 2>&1; then
        echo "ERROR: Failed to clone GCSFuse repository"
        exit 1
    fi
    
    cd "$GCSFUSE_DIR"
    
    if ! git checkout "$GCSFUSE_COMMIT" 2>&1; then
        echo "ERROR: Failed to checkout commit/branch: $GCSFUSE_COMMIT"
        exit 1
    fi
    
    echo "  Building GCSFuse binary..."
    if ! go build -o gcsfuse 2>&1; then
        echo "ERROR: GCSFuse build failed"
        exit 1
    fi
    
    GCSFUSE_BIN="$GCSFUSE_DIR/gcsfuse"
    if [ ! -f "$GCSFUSE_BIN" ]; then
        echo "ERROR: GCSFuse binary not created"
        exit 1
    fi
    
    cd "$WORKSPACE"
    echo "✓ GCSFuse binary ready: $GCSFUSE_BIN"
fi

# Cleanup any previous mounts and processes
echo "Checking for previous mounts and processes..."
MOUNT_DIR="$WORKSPACE/mnt"

##############################################################################
# SECTION 2: TEST EXECUTION
#
# Main execution flow:
# - Clean up any existing GCSFuse mounts and hanging processes
# - Start background cancellation monitor (checks GCS for cancel flag)
# - Initialize manifest.json for tracking test results
# - Execute tests based on mode:
#   * Single-config mode: Build GCSFuse once, run all assigned tests
#   * Multi-config mode: For each test, build specific GCSFuse commit and run
# - For each test:
#   * Mount GCSFuse with specified arguments
#   * Start resource monitoring (CPU, memory, page cache)
#   * Run FIO benchmark for specified iterations
#   * Stop monitoring and calculate metrics
#   * Upload results to GCS and update manifest
# - Upload final manifest with completion status
# - Upload worker logs to GCS
##############################################################################

# Unmount if already mounted
if mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
    echo "  Found existing mount at $MOUNT_DIR, unmounting..."
    fusermount -u "$MOUNT_DIR" 2>/dev/null || umount -f "$MOUNT_DIR" 2>/dev/null || true
    sleep 1
fi

# Kill any hanging GCSFuse processes
HANGING_PIDS=$(pgrep -f "gcsfuse" || true)
if [ -n "$HANGING_PIDS" ]; then
    echo "  Found hanging GCSFuse processes: $HANGING_PIDS"
    echo "  Killing hanging processes..."
    kill -9 $HANGING_PIDS 2>/dev/null || true
    sleep 1
fi

# Ensure mount directory exists
mkdir -p "$MOUNT_DIR"
echo "✓ Cleanup complete"

# Start background cancellation monitor
CANCEL_CHECK_PID=""
(
    while true; do
        CANCEL_FLAG="gs://${ARTIFACTS_BUCKET}/${BENCHMARK_ID}/cancel"
        if gcloud storage ls "$CANCEL_FLAG" > /dev/null 2>&1; then
            echo "========================================"
            echo "CANCELLATION DETECTED - Stopping worker"
            echo "========================================"
            # Cleanup before terminating
            cleanup_gcsfuse
            # Kill the main worker process
            kill -TERM $$ 2>/dev/null
            exit 0
        fi
        sleep 5
    done
) &
CANCEL_CHECK_PID=$!

echo "Started cancellation monitor (PID: $CANCEL_CHECK_PID)"

# Setup mount directory
MOUNT_DIR="$WORKSPACE/mnt"
mkdir -p "$MOUNT_DIR"

# Function to build GCSFuse for a specific commit (caching to avoid rebuilds)
# Function to build GCSFuse from a specific commit
build_gcsfuse_for_commit() {
    local COMMIT=$1
    local BUILD_DIR="$WORKSPACE/gcsfuse_${COMMIT}"
    
    # Check if already built
    if [ -f "$BUILD_DIR/gcsfuse" ]; then
        echo "$BUILD_DIR/gcsfuse"
        return 0
    fi
    
    # Send informational messages to stderr to avoid capturing them in command substitution
    echo "Building GCSFuse from commit: $COMMIT" >&2
    git clone https://github.com/GoogleCloudPlatform/gcsfuse.git "$BUILD_DIR" >&2 2>&1 | sed 's/^/  /' >&2
    cd "$BUILD_DIR"
    
    # Checkout the specified commit - fail if not found
    if ! git checkout "$COMMIT" >&2 2>&1; then
        echo "  ERROR: Failed to checkout commit/branch: $COMMIT" >&2
        cd "$WORKSPACE"
        return 1
    fi
    
    echo "  Building GCSFuse binary..." >&2
    go build -o gcsfuse >&2 2>&1
    
    if [ ! -f "gcsfuse" ]; then
        echo "  ERROR: Build failed - gcsfuse binary not created" >&2
        cd "$WORKSPACE"
        return 1
    fi
    
    cd "$WORKSPACE"
    echo "$BUILD_DIR/gcsfuse"
}

# Function to parse test parameters from CSV
parse_test_params() {
    local TEST_ID=$1
    local TEST_LINE
    local LINE_NUM
    
    # Calculate line number (TEST_ID + 1 to skip header)
    LINE_NUM=$((TEST_ID + 1))
    
    # Extract test parameters from CSV
    # CSV format: io_type,num_jobs,file_size,block_size,io_depth,nr_files
    TEST_LINE=$(awk -F',' -v line="$LINE_NUM" 'NR==line {print}' test-cases.csv)
    
    if [ -z "$TEST_LINE" ]; then
        echo "ERROR: Could not read test ID $TEST_ID (line $LINE_NUM) from test-cases.csv" >&2
        echo "Total lines in CSV: $(wc -l < test-cases.csv)" >&2
        return 1
    fi
    
    # Export variables for caller
    IO_TYPE=$(echo "$TEST_LINE" | cut -d',' -f1 | tr -d ' \r')
    THREADS=$(echo "$TEST_LINE" | cut -d',' -f2 | tr -d ' \r')
    FILE_SIZE=$(echo "$TEST_LINE" | cut -d',' -f3 | tr -d ' \r')
    BS=$(echo "$TEST_LINE" | cut -d',' -f4 | tr -d ' \r')
    IO_DEPTH=$(echo "$TEST_LINE" | cut -d',' -f5 | tr -d ' \r')
    NRFILES=$(echo "$TEST_LINE" | cut -d',' -f6 | tr -d ' \r')
    
    # Validate parameters
    if [ -z "$BS" ] || [ -z "$FILE_SIZE" ] || [ -z "$IO_DEPTH" ] || [ -z "$IO_TYPE" ] || [ -z "$THREADS" ] || [ -z "$NRFILES" ]; then
        echo "ERROR: Invalid or missing parameters from CSV line: $TEST_LINE" >&2
        return 1
    fi
    
    return 0
}

# Function to start resource monitoring
start_monitoring() {
    local GCSFUSE_PID=$1
    local MONITOR_FILE=$2
    local MONITOR_STOP_FLAG=$3
    local MONITOR_PID_FILE=$4
    
    rm -f "$MONITOR_STOP_FLAG"
    
    # Get number of CPUs for normalization
    NUM_CPUS=$(nproc)
    
    # Initialize previous values for CPU calculation
    PREV_PROC_TIME=0
    PREV_SYSTEM_TIME=0
    PREV_SYS_IDLE=0
    PREV_SYS_ACTIVE=0
    PREV_SYS_IOWAIT=0
    
    # Initialize network tracking variables
    PREV_NET_RX=0
    PREV_NET_TX=0
    
    # Start background monitoring process
    {
        while [ ! -f "$MONITOR_STOP_FLAG" ]; do
            if [ -d "/proc/$GCSFUSE_PID" ]; then
                TIMESTAMP=$(date +%s)
                
                # Get CPU using /proc/stat for accurate measurement
                # Read process CPU time (utime + stime in clock ticks)
                PROC_STAT=$(cat /proc/$GCSFUSE_PID/stat 2>/dev/null || echo "")
                if [ -n "$PROC_STAT" ]; then
                    PROC_UTIME=$(echo "$PROC_STAT" | awk '{print $14}')
                    PROC_STIME=$(echo "$PROC_STAT" | awk '{print $15}')
                    PROC_TIME=$((PROC_UTIME + PROC_STIME))
                    
                    # Read system total CPU time
                    SYSTEM_STAT=$(head -1 /proc/stat)
                    SYSTEM_TIME=$(echo "$SYSTEM_STAT" | awk '{sum=0; for(i=2;i<=NF;i++) sum+=$i; print sum}')
                    
                    # Calculate CPU percentage (if we have previous values)
                    if [ $PREV_PROC_TIME -gt 0 ]; then
                        PROC_DELTA=$((PROC_TIME - PREV_PROC_TIME))
                        SYSTEM_DELTA=$((SYSTEM_TIME - PREV_SYSTEM_TIME))
                        
                        if [ $SYSTEM_DELTA -gt 0 ]; then
                            # CPU% = (process_delta / system_delta) * 100
                            # system_delta already includes all cores, so no need to multiply by NUM_CPUS
                            CPU_PERCENT=$(echo "scale=2; ($PROC_DELTA * 100) / $SYSTEM_DELTA" | bc 2>/dev/null || echo "0")
                        else
                            CPU_PERCENT="0"
                        fi
                    else
                        CPU_PERCENT="0"
                    fi
                    
                    PREV_PROC_TIME=$PROC_TIME
                    PREV_SYSTEM_TIME=$SYSTEM_TIME
                else
                    CPU_PERCENT="0"
                fi
                
                # Get memory stats
                MEM_RSS_KB=$(ps -p $GCSFUSE_PID -o rss= 2>/dev/null || echo "0")
                MEM_VSZ_KB=$(ps -p $GCSFUSE_PID -o vsz= 2>/dev/null || echo "0")
                MEM_RSS_MB=$(echo "scale=2; $MEM_RSS_KB / 1024" | bc 2>/dev/null || echo "0")
                MEM_VSZ_MB=$(echo "scale=2; $MEM_VSZ_KB / 1024" | bc 2>/dev/null || echo "0")
                
                # Get page cache from /proc/meminfo
                PAGE_CACHE_KB=$(grep "^Cached:" /proc/meminfo | awk '{print $2}')
                PAGE_CACHE_GB=$(echo "scale=2; $PAGE_CACHE_KB / 1024 / 1024" | bc 2>/dev/null || echo "0")
                
                # Get network throughput from /proc/net/dev
                # Sum all interfaces' RX and TX bytes
                NET_STATS=$(awk 'NR>2 {rx+=$2; tx+=$10} END {print rx, tx}' /proc/net/dev 2>/dev/null || echo "0 0")
                NET_RX_BYTES=$(echo "$NET_STATS" | awk '{print $1}')
                NET_TX_BYTES=$(echo "$NET_STATS" | awk '{print $2}')
                
                # Calculate network throughput in MB/s
                if [ $PREV_NET_RX -gt 0 ]; then
                    NET_RX_DELTA=$((NET_RX_BYTES - PREV_NET_RX))
                    NET_TX_DELTA=$((NET_TX_BYTES - PREV_NET_TX))
                    # Convert bytes/2sec to MB/s: (bytes / 2) / 1048576
                    NET_RX_MBPS=$(echo "scale=2; $NET_RX_DELTA / 2 / 1048576" | bc 2>/dev/null || echo "0")
                    NET_TX_MBPS=$(echo "scale=2; $NET_TX_DELTA / 2 / 1048576" | bc 2>/dev/null || echo "0")
                else
                    NET_RX_MBPS="0"
                    NET_TX_MBPS="0"
                fi
                
                PREV_NET_RX=$NET_RX_BYTES
                PREV_NET_TX=$NET_TX_BYTES
                
                # Get overall system CPU usage from /proc/stat (more reliable than top)
                # Format: cpu  user nice system idle iowait irq softirq steal guest guest_nice
                # Awk fields: $1=label $2=user $3=nice $4=system $5=idle $6=iowait $7=irq $8=softirq $9=steal $10=guest $11=guest_nice
                SYS_STAT=$(head -1 /proc/stat)
                SYS_IDLE=$(echo "$SYS_STAT" | awk '{print $5}')
                SYS_IOWAIT=$(echo "$SYS_STAT" | awk '{print $6}')
                # Calculate active time (all fields except idle and iowait)
                SYS_ACTIVE=$(echo "$SYS_STAT" | awk '{sum=0; for(i=2;i<=NF;i++) {if(i!=5 && i!=6) sum+=$i} print sum}')
                
                # Calculate overall system CPU utilization percentage (standard method)
                # iowait is excluded from active time but included in total time
                if [ $PREV_SYS_ACTIVE -gt 0 ]; then
                    SYS_IDLE_DELTA=$((SYS_IDLE - PREV_SYS_IDLE))
                    SYS_IOWAIT_DELTA=$((SYS_IOWAIT - PREV_SYS_IOWAIT))
                    SYS_ACTIVE_DELTA=$((SYS_ACTIVE - PREV_SYS_ACTIVE))
                    SYS_TOTAL_DELTA=$((SYS_ACTIVE_DELTA + SYS_IDLE_DELTA + SYS_IOWAIT_DELTA))
                    
                    if [ $SYS_TOTAL_DELTA -gt 0 ]; then
                        # System CPU% = (active_delta / total_delta * 100)
                        # This is the standard calculation: active work as % of total time
                        # iowait is NOT counted as active CPU work, but IS included in total time
                        SYSTEM_CPU=$(echo "scale=2; ($SYS_ACTIVE_DELTA * 100) / $SYS_TOTAL_DELTA" | bc 2>/dev/null || echo "0")
                    else
                        SYSTEM_CPU="0"
                    fi
                else
                    SYSTEM_CPU="0"
                fi
                
                PREV_SYS_IDLE=$SYS_IDLE
                PREV_SYS_IOWAIT=$SYS_IOWAIT
                PREV_SYS_ACTIVE=$SYS_ACTIVE
                
                echo "$TIMESTAMP,$CPU_PERCENT,$MEM_RSS_MB,$MEM_VSZ_MB,$PAGE_CACHE_GB,$SYSTEM_CPU,$NET_RX_MBPS,$NET_TX_MBPS" >> "$MONITOR_FILE"
            fi
            sleep 2
        done
    } &
    
    # Write PID to file
    echo $! > "$MONITOR_PID_FILE"
}

# Function to stop resource monitoring
stop_monitoring() {
    local MONITOR_PID=$1
    local MONITOR_STOP_FLAG=$2
    
    touch "$MONITOR_STOP_FLAG"
    sleep 1
    kill $MONITOR_PID 2>/dev/null || true
    wait $MONITOR_PID 2>/dev/null || true
}

# Function to calculate resource metrics
calculate_metrics() {
    local MONITOR_FILE=$1
    
    if [ ! -f "$MONITOR_FILE" ]; then
        echo "0" "0" "0" "0" "0" "0" "0" "0" "0" "0"
        return
    fi
    
    AVG_CPU=$(awk -F',' 'NR>1 {sum+=$2; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
    MAX_CPU=$(awk -F',' 'NR>1 {if($2>max) max=$2} END {printf "%.2f", max+0}' "$MONITOR_FILE")
    AVG_MEM_RSS=$(awk -F',' 'NR>1 {sum+=$3; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
    MAX_MEM_RSS=$(awk -F',' 'NR>1 {if($3>max) max=$3} END {printf "%.2f", max+0}' "$MONITOR_FILE")
    AVG_PAGE_CACHE=$(awk -F',' 'NR>1 {sum+=$5; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
    MAX_PAGE_CACHE=$(awk -F',' 'NR>1 {if($5>max) max=$5} END {printf "%.2f", max+0}' "$MONITOR_FILE")
    AVG_SYS_CPU=$(awk -F',' 'NR>1 {sum+=$6; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
    MAX_SYS_CPU=$(awk -F',' 'NR>1 {if($6>max) max=$6} END {printf "%.2f", max+0}' "$MONITOR_FILE")
    AVG_NET_RX=$(awk -F',' 'NR>1 {sum+=$7; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
    MAX_NET_RX=$(awk -F',' 'NR>1 {if($7>max) max=$7} END {printf "%.2f", max+0}' "$MONITOR_FILE")
    AVG_NET_TX=$(awk -F',' 'NR>1 {sum+=$8; count++} END {if(count>0) printf "%.2f", sum/count; else print "0"}' "$MONITOR_FILE")
    MAX_NET_TX=$(awk -F',' 'NR>1 {if($8>max) max=$8} END {printf "%.2f", max+0}' "$MONITOR_FILE")
    
    echo "$AVG_CPU" "$MAX_CPU" "$AVG_MEM_RSS" "$MAX_MEM_RSS" "$AVG_PAGE_CACHE" "$MAX_PAGE_CACHE" "$AVG_SYS_CPU" "$MAX_SYS_CPU" "$AVG_NET_RX" "$MAX_NET_RX" "$AVG_NET_TX" "$MAX_NET_TX"
}

# Function to run FIO test iterations
run_test_iterations() {
    local TEST_DIR=$1
    local FIO_JOB=$2
    local MONITOR_FILE=$3
    local GCSFUSE_BIN_PATH=$4
    local MOUNT_ARGS=$5
    
    # Run FIO iterations with mount/unmount for each
    for ((i=1; i<=ITERATIONS; i++)); do
        echo "  Iteration $i/$ITERATIONS"
        
        # Mount GCSFuse with logging enabled
        echo "    Mounting GCSFuse..."
        GCSFUSE_LOG_FILE="$TEST_DIR/gcsfuse_mount_${i}.log"
        $GCSFUSE_BIN_PATH $MOUNT_ARGS \
            --log-format text \
            --log-severity info \
            --log-file "$GCSFUSE_LOG_FILE" \
            "$BUCKET" "$MOUNT_DIR"
        
        # Get GCSFuse PID for monitoring
        GCSFUSE_PID=$(pgrep -f "gcsfuse.*${MOUNT_DIR}" | head -1)
        echo "    GCSFuse PID: $GCSFUSE_PID"
        
        # Start monitoring
        echo "    Starting resource monitoring..."
        MONITOR_STOP_FLAG="$TEST_DIR/monitor_stop_${i}"
        MONITOR_PID_FILE="$TEST_DIR/monitor_pid_${i}"
        start_monitoring "$GCSFUSE_PID" "$MONITOR_FILE" "$MONITOR_STOP_FLAG" "$MONITOR_PID_FILE"
        # Wait for PID file to be created
        for attempt in {1..10}; do
            if [ -f "$MONITOR_PID_FILE" ]; then
                MONITOR_PID=$(cat "$MONITOR_PID_FILE")
                echo "    Monitor PID: $MONITOR_PID"
                break
            fi
            sleep 0.1
        done
        
        # Create subdirectory for this file size and populate metadata cache
        mkdir -p "$TEST_DATA_DIR"
        echo "    Populating metadata cache for $TEST_DATA_DIR..."
        if ! time ls -R "$TEST_DATA_DIR" 1> /dev/null 2>&1; then
            echo "    Warning: ls -R failed, directory may not exist in GCS"
        fi
        
        # Clear page cache before FIO run to ensure consistent results
        echo "    Clearing page cache..."
        sync
        sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || echo "    Warning: Failed to clear page cache (requires sudo)"
        
        # Run FIO
        echo "    Running FIO benchmark..."
        OUTPUT_FILE="${TEST_DIR}/fio_output_${i}.json"
        if ! fio "$FIO_JOB" --output-format=json --output="$OUTPUT_FILE"; then
            echo "ERROR: FIO execution failed for iteration $i" >&2
            stop_monitoring "$MONITOR_PID" "$MONITOR_STOP_FLAG"
            fusermount -u "$MOUNT_DIR" 2>/dev/null || umount "$MOUNT_DIR" 2>/dev/null || true
            return 1
        fi
        echo "    FIO completed successfully"
        
        # Stop monitoring
        stop_monitoring "$MONITOR_PID" "$MONITOR_STOP_FLAG"
        
        # Unmount GCSFuse
        echo "    Unmounting GCSFuse..."
        fusermount -u "$MOUNT_DIR" 2>/dev/null || umount "$MOUNT_DIR" 2>/dev/null || true
        
        # Clear page cache after unmount to ensure clean state for next iteration
        echo "    Clearing page cache after unmount..."
        sync
        sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || echo "    Warning: Failed to clear page cache (requires sudo)"
        
        # Wait before next iteration
        if [ $i -lt $ITERATIONS ]; then
            sleep 2
        fi
    done
}

# Unified function to execute a single test (works for both single-config and multi-config)
execute_test() {
    local TEST_ID=$1
    local TEST_DIR_NAME=$2
    local GCSFUSE_BIN_PATH=$3
    local MOUNT_ARGS=$4
    local MANIFEST_ENTRY_TYPE=$5  # "single" or "multi"
    
    # Optional multi-config parameters
    local MATRIX_ID=${6:-""}
    local CONFIG_ID=${7:-""}
    local CONFIG_LABEL=${8:-""}
    local COMMIT=${9:-""}
    
    echo "=========================================="
    if [ "$MANIFEST_ENTRY_TYPE" = "multi" ]; then
        echo "Running Matrix ID: $MATRIX_ID (Test $TEST_ID, Config $CONFIG_ID: $CONFIG_LABEL)"
        echo "Commit: $COMMIT"
        echo "Mount args: $MOUNT_ARGS"
    else
        echo "Running Test ID: $TEST_ID"
    fi
    
    # Parse and validate test parameters
    if ! parse_test_params "$TEST_ID"; then
        echo "ERROR: Failed to parse test parameters for test $TEST_ID" >&2
        return 1
    fi
    
    echo "Parameters: bs=$BS, file_size=$FILE_SIZE, io_depth=$IO_DEPTH, io_type=$IO_TYPE, threads=$THREADS, nrfiles=$NRFILES"
    
    TEST_DIR="test-${TEST_DIR_NAME}"
    mkdir -p "$TEST_DIR"
    
    # Create FIO job file from template
    FIO_JOB="$TEST_DIR/job.fio"
    TEST_DATA_DIR="$MOUNT_DIR/$FILE_SIZE"
    export BS FILE_SIZE IO_DEPTH IO_TYPE THREADS NRFILES TEST_DATA_DIR
    envsubst '$BS $FILE_SIZE $IO_DEPTH $IO_TYPE $THREADS $NRFILES $TEST_DATA_DIR' < jobfile.fio > "$FIO_JOB"
    
    echo "FIO job file created at: $FIO_JOB"
    echo "FIO job contents:"
    cat "$FIO_JOB"
    echo "---"
    
    # Initialize monitoring file
    MONITOR_FILE="$TEST_DIR/monitor.log"
    echo "timestamp,cpu_percent,mem_rss_mb,mem_vsz_mb,page_cache_gb,system_cpu_percent,net_rx_mbps,net_tx_mbps" > "$MONITOR_FILE"
    
    # Run test iterations
    if ! run_test_iterations "$TEST_DIR" "$FIO_JOB" "$MONITOR_FILE" "$GCSFUSE_BIN_PATH" "$MOUNT_ARGS"; then
        echo "ERROR: Test iterations failed for test $TEST_ID" >&2
        return 1
    fi
    
    # Calculate and report resource metrics
    read AVG_CPU MAX_CPU AVG_MEM_RSS MAX_MEM_RSS AVG_PAGE_CACHE MAX_PAGE_CACHE AVG_SYS_CPU MAX_SYS_CPU AVG_NET_RX MAX_NET_RX AVG_NET_TX MAX_NET_TX < <(calculate_metrics "$MONITOR_FILE")
    echo "  Resource Usage - Avg CPU: ${AVG_CPU}%, Peak CPU: ${MAX_CPU}%, Avg Memory: ${AVG_MEM_RSS}MB, Peak Memory: ${MAX_MEM_RSS}MB"
    echo "  Network Usage - Avg RX: ${AVG_NET_RX} MB/s, Peak RX: ${MAX_NET_RX} MB/s, Avg TX: ${AVG_NET_TX} MB/s, Peak TX: ${MAX_NET_TX} MB/s"
    echo "                   Avg Page Cache: ${AVG_PAGE_CACHE}GB, Peak Page Cache: ${MAX_PAGE_CACHE}GB"
    echo "                   Avg System CPU: ${AVG_SYS_CPU}%, Peak System CPU: ${MAX_SYS_CPU}%"
    
    # Upload test results
    gcloud storage cp -r "$TEST_DIR" "${RESULT_BASE}/"
    
    # Build manifest entry based on mode
    if [ "$MANIFEST_ENTRY_TYPE" = "multi" ]; then
        # Multi-config: include config information and test_id in params
        TEST_PARAMS="{\"test_id\":\"$TEST_ID\",\"bs\":\"$BS\",\"file_size\":\"$FILE_SIZE\",\"io_depth\":\"$IO_DEPTH\",\"io_type\":\"$IO_TYPE\",\"threads\":\"$THREADS\",\"nrfiles\":\"$NRFILES\",\"config_id\":\"$CONFIG_ID\",\"config_label\":\"$CONFIG_LABEL\",\"commit\":\"$COMMIT\",\"mount_args\":\"$MOUNT_ARGS\",\"avg_cpu\":\"$AVG_CPU\",\"peak_cpu\":\"$MAX_CPU\",\"avg_mem_mb\":\"$AVG_MEM_RSS\",\"peak_mem_mb\":\"$MAX_MEM_RSS\",\"avg_page_cache_gb\":\"$AVG_PAGE_CACHE\",\"peak_page_cache_gb\":\"$MAX_PAGE_CACHE\",\"avg_sys_cpu\":\"$AVG_SYS_CPU\",\"peak_sys_cpu\":\"$MAX_SYS_CPU\",\"avg_net_rx_mbps\":\"$AVG_NET_RX\",\"peak_net_rx_mbps\":\"$MAX_NET_RX\",\"avg_net_tx_mbps\":\"$AVG_NET_TX\",\"peak_net_tx_mbps\":\"$MAX_NET_TX\"}"
        jq ".tests += [{\"matrix_id\":$MATRIX_ID,\"test_id\":$TEST_ID,\"config_id\":$CONFIG_ID,\"status\":\"success\",\"params\":$TEST_PARAMS}]" manifest.json > manifest_tmp.json
        mv manifest_tmp.json manifest.json
        echo "  ✓ Matrix test $MATRIX_ID completed"
    else
        # Single-config: simpler manifest entry
        TEST_PARAMS="{\"bs\":\"$BS\",\"file_size\":\"$FILE_SIZE\",\"io_depth\":\"$IO_DEPTH\",\"io_type\":\"$IO_TYPE\",\"threads\":\"$THREADS\",\"nrfiles\":\"$NRFILES\",\"avg_cpu\":\"$AVG_CPU\",\"peak_cpu\":\"$MAX_CPU\",\"avg_mem_mb\":\"$AVG_MEM_RSS\",\"peak_mem_mb\":\"$MAX_MEM_RSS\",\"avg_page_cache_gb\":\"$AVG_PAGE_CACHE\",\"peak_page_cache_gb\":\"$MAX_PAGE_CACHE\",\"avg_sys_cpu\":\"$AVG_SYS_CPU\",\"peak_sys_cpu\":\"$MAX_SYS_CPU\",\"avg_net_rx_mbps\":\"$AVG_NET_RX\",\"peak_net_rx_mbps\":\"$MAX_NET_RX\",\"avg_net_tx_mbps\":\"$AVG_NET_TX\",\"peak_net_tx_mbps\":\"$MAX_NET_TX\"}"
        jq ".tests += [{\"test_id\":$TEST_ID,\"status\":\"success\",\"params\":$TEST_PARAMS}]" manifest.json > manifest_tmp.json
        mv manifest_tmp.json manifest.json
        echo "  ✓ Test $TEST_ID completed"
    fi
    
    return 0
}

# Track number of successfully completed tests
TESTS_COMPLETED=0

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
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

# Determine test list based on mode
if [ "$MODE" = "single-config" ]; then
    TEST_IDS=$(jq -r '.test_ids | join(" ")' job.json)
    echo "Test IDs: $TEST_IDS"
else
    # Multi-config: extract test_entries
    TEST_ENTRIES=$(jq -c '.test_entries[]' job.json)
    echo "Test entries loaded from job spec"
fi

# Run each assigned test
if [ "$MODE" = "single-config" ]; then
    # Single-config mode: iterate over test IDs
    for TEST_ID in $TEST_IDS; do
        if execute_test "$TEST_ID" "$TEST_ID" "$GCSFUSE_BIN" "$GCSFUSE_MOUNT_ARGS" "single"; then
            TESTS_COMPLETED=$((TESTS_COMPLETED + 1))
        else
            echo "ERROR: Test $TEST_ID failed" >&2
            exit 1
        fi
    done
else
    # Multi-config mode: iterate over test_entries
    while IFS= read -r ENTRY; do
        MATRIX_ID=$(echo "$ENTRY" | jq -r '.matrix_id')
        TEST_ID=$(echo "$ENTRY" | jq -r '.test_id')
        CONFIG_ID=$(echo "$ENTRY" | jq -r '.config_id')
        COMMIT=$(echo "$ENTRY" | jq -r '.commit')
        MOUNT_ARGS=$(echo "$ENTRY" | jq -r '.mount_args')
        CONFIG_LABEL=$(echo "$ENTRY" | jq -r '.config_label')
        
        # Build GCSFuse for this config (cached)
        echo "Building/using GCSFuse for commit: $COMMIT"
        GCSFUSE_BIN=$(build_gcsfuse_for_commit "$COMMIT")
        
        if [ -z "$GCSFUSE_BIN" ] || [ ! -f "$GCSFUSE_BIN" ]; then
            echo "ERROR: Failed to build GCSFuse for commit: $COMMIT" >&2
            continue
        fi
        
        echo "  Using GCSFuse binary: $GCSFUSE_BIN"
        
        # Execute test using unified function
        if execute_test "$TEST_ID" "$MATRIX_ID" "$GCSFUSE_BIN" "$MOUNT_ARGS" "multi" "$MATRIX_ID" "$CONFIG_ID" "$CONFIG_LABEL" "$COMMIT"; then
            TESTS_COMPLETED=$((TESTS_COMPLETED + 1))
        else
            echo "WARNING: Test $MATRIX_ID failed, continuing with remaining tests" >&2
        fi
    done < <(echo "$TEST_ENTRIES")
fi

# Stop cancellation monitor
if [ -n "$CANCEL_CHECK_PID" ]; then
    kill $CANCEL_CHECK_PID 2>/dev/null || true
fi

# Finalize manifest
END_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S%z")

if [ "$MODE" = "single-config" ]; then
    TOTAL_TESTS=$(echo "$TEST_IDS" | wc -w)
else
    TOTAL_TESTS=$(echo "$TEST_ENTRIES" | wc -l)
fi

# Validate that tests were actually executed
if [ $TESTS_COMPLETED -eq 0 ]; then
    echo "ERROR: No tests were successfully completed!" >&2
    jq ".status = \"failed\" | .end_time = \"$END_TIME\" | .total_tests = $TOTAL_TESTS | .completed_tests = 0 | .error = \"No tests completed\"" manifest.json > manifest_tmp.json
    mv manifest_tmp.json manifest.json
    gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"
    exit 1
fi

# Check if all tests completed
if [ $TESTS_COMPLETED -ne $TOTAL_TESTS ]; then
    echo "WARNING: Only $TESTS_COMPLETED of $TOTAL_TESTS tests completed" >&2
    jq ".status = \"partial\" | .end_time = \"$END_TIME\" | .total_tests = $TOTAL_TESTS | .completed_tests = $TESTS_COMPLETED" manifest.json > manifest_tmp.json
else
    jq ".status = \"completed\" | .end_time = \"$END_TIME\" | .total_tests = $TOTAL_TESTS | .completed_tests = $TESTS_COMPLETED" manifest.json > manifest_tmp.json
fi

mv manifest_tmp.json manifest.json

# Upload final manifest
gcloud storage cp manifest.json "${RESULT_BASE}/manifest.json"

# Disable error trap for successful completion
trap - ERR EXIT

echo "=========================================="
echo "✓ Tests completed: $TESTS_COMPLETED/$TOTAL_TESTS"
echo "Results uploaded to: $RESULT_BASE"
