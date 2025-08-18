#!/bin/bash

# Script to test gcsfuse buffered-read feature with FIO benchmarks
# Tests single-threaded, 10GiB file, sequential read with 1MB block-size
# Runs each test configuration 3 times and calculates average

set -e  # Exit on any error
# set -x  # Enable command tracing for debugging

# Default values - can be overridden by environment variables
GCSFUSE_BUCKET=${GCSFUSE_BUCKET:-"princer-grpc-write-test-uw4a"}
MOUNT_POINT=${MOUNT_POINT:-"$HOME/brgcs"}
OUTPUT_FILE=${OUTPUT_FILE:-"buffered_read_benchmark_$(date +%Y%m%d_%H%M%S).txt"}
TEST_FILE_SIZE=${TEST_FILE_SIZE:-"10G"}
BLOCK_SIZE=${BLOCK_SIZE:-"1M"}

# Buffered read configurations to test
# declare -a BLOCK_SIZE_MB_VALUES=(2 4 6 8 10 12 14 16 18 20 22 24 26 28 30 32)
# declare -a BLOCK_SIZE_MB_VALUES=(16 32 48 64 80 96 112)
# declare -a MAX_READ_BLOCK_HANDLES=(2 4 6 8 10 12 14 16 18 20 22 24 26 28 30 32)
declare -a BLOCK_SIZE_MB_VALUES=(32)
declare -a MAX_READ_BLOCK_HANDLES=(14)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

# Function to clean up on exit
cleanup() {
    print_status "Cleaning up..."
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        fusermount -u "$MOUNT_POINT" || umount "$MOUNT_POINT" || true
    fi
}

# Set up cleanup trap
trap cleanup EXIT

# Function to create FIO job file
create_fio_job() {
    local job_file="$1"
    cat > "$job_file" << EOF
[global]
# I/O Engine and Behavior
ioengine=libaio
direct=1
fadvise_hint=0
verify=0
iodepth=1
invalidate=1
time_based=0

nrfiles=30
numjobs=1
thread=1
fsync=1
openfiles=1
group_reporting=1

[10gb_single_thread]
rw=read
bs=${BLOCK_SIZE}
# File configuration
directory=${MOUNT_POINT}
filename_format=test_file.\$jobnum.\$filenum
filesize=${TEST_FILE_SIZE}
EOF
}

# Function to parse FIO JSON output and extract bandwidth
parse_fio_output() {
    local fio_output="$1"
    # Extract read bandwidth in KB/s from JSON output
    echo "$fio_output" | jq -r '.jobs[0].read.bw // 0'
}

# Function to run a single FIO test
run_single_test() {
    local block_size_mb=$1
    local max_handles=$2
    local iteration=$3
    
    print_status "Running iteration $iteration with block-size-mb=$block_size_mb, max-handles=$max_handles" >&2
    
    # Unmount if already mounted
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        fusermount -u "$MOUNT_POINT" || umount "$MOUNT_POINT" || true
    fi
    
    # Create mount point if it doesn't exist
    mkdir -p "$MOUNT_POINT"
    
    # Mount with buffered read configuration
    local mount_cmd="gcsfuse --implicit-dirs --enable-buffered-read --client-protocol grpc --enable-cloud-profiling --profiling-label=buffered-read-6-$iteration"
    mount_cmd+=" --read-global-max-blocks 100"
    mount_cmd+=" --read-block-size-mb $block_size_mb"
    mount_cmd+=" --read-max-blocks-per-handle $max_handles"
    mount_cmd+=" $GCSFUSE_BUCKET $MOUNT_POINT"
    
    print_status "Mounting: $mount_cmd" >&2
    eval "$mount_cmd" >&2
    
    # Wait for mount to stabilize
    sleep 5
    
    # Create FIO job file
    local fio_job="/tmp/buffered_read_test.fio"
    create_fio_job "$fio_job"
    
    # Run FIO test
    print_status "Starting FIO test..." >&2
    local fio_output
    fio_output=$(fio "$fio_job" --output-format=json 2>&1)
    local fio_exit_code=$?
    
    if [ $fio_exit_code -ne 0 ]; then
        print_error "FIO test failed with exit code $fio_exit_code" >&2
        echo "$fio_output" >&2
        return 1
    fi
    
    # Parse bandwidth result
    local bandwidth
    bandwidth=$(parse_fio_output "$fio_output")
    
    if [ "$bandwidth" = "0" ] || [ -z "$bandwidth" ]; then
        print_warning "Could not parse bandwidth from FIO output, extracting manually..." >&2
        # Fallback: extract bandwidth using grep and awk
        bandwidth=$(echo "$fio_output" | grep -E "read.*BW=" | awk -F'BW=' '{print $2}' | awk '{print $1}' | sed 's/[^0-9.]//g' | head -1)
    fi
    
    # Convert bandwidth from KB/s to MB/s (divide by 1000)
    local bandwidth_mb
    bandwidth_mb=$(awk "BEGIN {printf \"%.2f\", $bandwidth / 1000}")
    
    print_success "Test completed. Bandwidth: ${bandwidth_mb} MB/s (${bandwidth} KB/s)" >&2
    
    # Log detailed results
    echo "=== Iteration $iteration: block-size-mb=$block_size_mb, max-handles=$max_handles ===" >> "$OUTPUT_FILE"
    echo "Timestamp: $(date)" >> "$OUTPUT_FILE"
    echo "Mount command: $mount_cmd" >> "$OUTPUT_FILE"
    echo "Bandwidth: ${bandwidth_mb} MB/s (${bandwidth} KB/s)" >> "$OUTPUT_FILE"
    # echo "FIO Output:" >> "$OUTPUT_FILE"
    # echo "$fio_output" >> "$OUTPUT_FILE"
    # echo "======================================" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    
    # Cleanup
    rm -f "$fio_job"
    fusermount -u "$MOUNT_POINT" || umount "$MOUNT_POINT" || true
    
    # Only output the bandwidth value to stdout (this is what gets captured)
    echo "$bandwidth_mb"
}

# Function to calculate average of three values
calculate_average() {
    local val1=$1
    local val2=$2
    local val3=$3
    
    # Use awk for floating point arithmetic
    awk "BEGIN {printf \"%.2f\", ($val1 + $val2 + $val3) / 3}"
}

# Function to run complete test suite
run_test_suite() {
    print_status "Starting buffered read benchmark test suite"
    print_status "Test configuration:"
    print_status "  File size: $TEST_FILE_SIZE"
    print_status "  Block size: $BLOCK_SIZE"
    print_status "  Runtime per test: $RUNTIME"
    print_status "  Warmup time: $WARMUP_TIME"
    print_status "  Output file: $OUTPUT_FILE"
    
    # Initialize results file
    echo "GCSFuse Buffered Read Benchmark Results" > "$OUTPUT_FILE"
    echo "Generated on: $(date)" >> "$OUTPUT_FILE"
    echo "Test configuration: Single-threaded, ${TEST_FILE_SIZE} file, sequential read, ${BLOCK_SIZE} block-size" >> "$OUTPUT_FILE"
    echo "Each test run 3 times, average reported" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    
    # Results summary table header
    echo "=== SUMMARY RESULTS ===" >> "$OUTPUT_FILE"
    printf "%-15s %-20s %-15s %-15s %-15s %-15s\n" "BlockSize(MB)" "MaxHandles" "Run1(MB/s)" "Run2(MB/s)" "Run3(MB/s)" "Average(MB/s)" >> "$OUTPUT_FILE"
    echo "$(printf '%.0s-' {1..95})" >> "$OUTPUT_FILE"
    
    # Run tests for each configuration
    for block_size_mb in "${BLOCK_SIZE_MB_VALUES[@]}"; do
        for max_handles in "${MAX_READ_BLOCK_HANDLES[@]}"; do
            print_status "Testing configuration: block-size-mb=$block_size_mb, max-handles=$max_handles"
            
            # Run 3 iterations
            local results=()
            for i in {1..3}; do
                local result
                result=$(run_single_test "$block_size_mb" "$max_handles" "$i")
                results+=("$result")
                
                # Wait between iterations
                if [ $i -lt 3 ]; then
                    print_status "Waiting 30 seconds before next iteration..."
                    # sleep 30
                fi
            done
            
            # Calculate average
            local average
            average=$(calculate_average "${results[0]}" "${results[1]}" "${results[2]}")
            
            # Log summary
            printf "%-15s %-20s %-15s %-15s %-15s %-15s\n" \
                "$block_size_mb" "$max_handles" "${results[0]}" "${results[1]}" "${results[2]}" "$average" >> "$OUTPUT_FILE"
            
            print_success "Configuration completed. Average bandwidth: ${average} MB/s"
            
            # Wait between configurations
            print_status "Waiting 60 seconds before next configuration..."
            # sleep 60
        done
    done
    
    print_success "All tests completed! Results saved to: $OUTPUT_FILE"
}

# Function to display usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -b, --bucket BUCKET    GCS bucket name (default: $GCSFUSE_BUCKET)"
    echo "  -m, --mount MOUNT      Mount point path (default: $MOUNT_POINT)"
    echo "  -o, --output OUTPUT    Output file path (default: auto-generated)"
    echo "  -s, --size SIZE        Test file size (default: $TEST_FILE_SIZE)"
    echo "  -r, --runtime RUNTIME  Runtime per test (default: $RUNTIME)"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Environment variables:"
    echo "  GCSFUSE_BUCKET        GCS bucket name"
    echo "  MOUNT_POINT           Mount point path"
    echo "  OUTPUT_FILE           Output file path"
    echo "  TEST_FILE_SIZE        Test file size"
    echo "  RUNTIME               Runtime per test"
    echo ""
    echo "Example:"
    echo "  $0 --bucket my-test-bucket --runtime 10m"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -b|--bucket)
            GCSFUSE_BUCKET="$2"
            shift 2
            ;;
        -m|--mount)
            MOUNT_POINT="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        -s|--size)
            TEST_FILE_SIZE="$2"
            shift 2
            ;;
        -r|--runtime)
            RUNTIME="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate required tools
for tool in fio gcsfuse jq; do
    if ! command -v "$tool" &> /dev/null; then
        print_error "Required tool '$tool' is not installed or not in PATH"
        exit 1
    fi
done

# Validate bucket name
if [ -z "$GCSFUSE_BUCKET" ] || [ "$GCSFUSE_BUCKET" = "your-test-bucket" ]; then
    print_error "Please set a valid GCS bucket name using -b/--bucket option or GCSFUSE_BUCKET environment variable"
    exit 1
fi

# Main execution
main() {
    print_status "Starting GCSFuse buffered read benchmark"
    run_test_suite
    print_success "Benchmark completed successfully!"
}

# Run main function
main "$@"
