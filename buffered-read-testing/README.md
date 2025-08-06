# GCSFuse Buffered Read Benchmark

This directory contains tools to benchmark the GCSFuse buffered-read feature with FIO.

## Files

- `run_fio_benchmark.sh` - Main benchmark script that tests various buffered-read configurations
- `sequential_read_10g.fio` - FIO job file for single-threaded sequential read of 10GiB file with 1MB block-size

## Prerequisites

Make sure you have the following tools installed:
- `fio` - For I/O benchmarking
- `gcsfuse` - For mounting GCS buckets
- `jq` - For parsing JSON output

## Usage

### Quick Start

```bash
# Set your GCS bucket name
export GCSFUSE_BUCKET="your-test-bucket-name"

# Run the benchmark
./run_fio_benchmark.sh
```

### Advanced Usage

```bash
# Run with custom settings
./run_fio_benchmark.sh \
  --bucket "my-test-bucket" \
  --runtime "10m" \
  --output "my_results.txt"
```

### Command Line Options

- `-b, --bucket BUCKET`: GCS bucket name (required)
- `-m, --mount MOUNT`: Mount point path (default: `$HOME/gcsfuse-mount`)
- `-o, --output OUTPUT`: Output file path (default: auto-generated with timestamp)
- `-s, --size SIZE`: Test file size (default: `10G`)
- `-r, --runtime RUNTIME`: Runtime per test (default: `5m`)
- `-h, --help`: Show help message

### Environment Variables

You can also set configuration via environment variables:
- `GCSFUSE_BUCKET` - GCS bucket name
- `MOUNT_POINT` - Mount point path
- `OUTPUT_FILE` - Output file path
- `TEST_FILE_SIZE` - Test file size
- `RUNTIME` - Runtime per test

## Test Configuration

The script tests the following buffered-read configurations:

### Block Size (MB)
- 1 MB
- 2 MB
- 4 MB
- 8 MB
- 16 MB
- 32 MB

### Max Read Block Handles
- 100
- 200
- 500
- 1000

## Test Process

For each configuration combination:
1. Mounts GCS bucket with specific buffered-read settings
2. Runs FIO test 3 times
3. Calculates average bandwidth
4. Unmounts and waits before next test
5. Logs detailed results

## Output

The script generates:
1. **Console output**: Real-time progress with colored status messages
2. **Results file**: Detailed logs and summary table with:
   - Individual run results
   - Average bandwidth for each configuration
   - Complete FIO output for debugging

### Sample Output Format

```
BlockSize(MB)   MaxHandles          Run1(MB/s)      Run2(MB/s)      Run3(MB/s)      Average(MB/s)
-----------------------------------------------------------------------------------------------
1               100                 120.56          121.67          122.78          121.67
1               200                 131.57          132.68          133.79          132.68
...
```

## Manual Testing

You can also run individual tests manually:

```bash
# Set environment variables
export MOUNT_POINT="$HOME/gcsfuse-mount"
export GCSFUSE_BUCKET="your-bucket"

# Mount with specific configuration
gcsfuse --implicit-dirs --client-protocol grpc \
  --experimental-enable-streaming-reads \
  --block-size-in-mb 8 \
  --max-read-block-handle 500 \
  $GCSFUSE_BUCKET $MOUNT_POINT

# Run FIO test
fio sequential_read_10g.fio

# Unmount
fusermount -u $MOUNT_POINT
```

## Notes

- Each test takes approximately 5-6 minutes (5m runtime + 30s warmup)
- Complete test suite with all configurations takes ~2-3 hours
- The script includes automatic cleanup and error handling
- Results are timestamped and saved to unique files
- Make sure your GCS bucket has sufficient space for the 10GiB test file
