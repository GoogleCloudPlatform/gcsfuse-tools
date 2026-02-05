# Distributed Micro-Benchmarking

This repository contains the worker scripts for a distributed micro-benchmarking system designed to test **GCSFuse** performance and behavior under various configurations. The monolithic worker has been refactored into a modular architecture for improved maintainability, readability, and debugging.

## System Components

The system consists of two primary roles acting in tandem:

| Component | Description |
| :--- | :--- |
| **Orchestrator** | Manages the lifecycle of the benchmark. It distributes configurations via GCS, triggers workers, and aggregates final results. |
| **Worker** | The execution engine residing on the Target VM. It prepares the environment, builds GCSFuse, executes FIO jobs, and monitors system health. |

## Architecture

* **Orchestrator (`orchestrator.py`)** - Coordinates benchmark execution, distributes test cases, monitors progress, aggregates results
* **Worker (`workers/worker.sh`)** - Runs on each VM, executes assigned tests, uploads results
* **Coordination** - GCS-based with job files and manifest tracking

### Output Organization

All benchmark results are organized by benchmark ID:

```text
results/
└── {benchmark-id}/
    ├── test-cases.csv       # Input: Test cases used
    ├── configs.csv          # Input: Config variations (if multi-config)
    ├── jobfile.fio          # Input: FIO template used
    ├── run-config.json      # Metadata: Run parameters and settings
    ├── combined_report.csv  # Output: Aggregated results
    ├── config1_report.csv   # Output: Per-config reports (if --separate-configs)
```

## Bucket Structure

Results are stored in the artifacts bucket under the specific benchmark ID and VM name:

```text
gs://<BUCKET>/<BENCHMARK_ID>/results/<VM_NAME>/
├── manifest.json        # Summary of all tests, status, and high-level metrics
└── test-<TEST_ID>/      # Directory for individual test data
    ├── fio_output_*.json    # Raw JSON output from FIO
    ├── monitor.log          # Time-series CSV of resource usage
    └── gcsfuse_mount_*.log  # Debug logs from GCSFuse
```

### Benefits

* Self-contained: All inputs and outputs in one directory
* `run-config.json` captures exact parameters for reproducibility
* Easy to organize different benchmark types by using descriptive benchmark IDs

### Worker Modules

The worker codebase is split into five distinct modules:

* **`worker.sh`**
  The main entry point. It orchestrates the entire workflow, manages the workspace, handles global error reporting, and uploads the final results.

* **`setup.sh`**
  Handles system preparation. It efficiently manages dependencies (checking for Git, Go, FIO, `bc`, `jq`, etc.) and ensures the environment is ready for testing.

* **`build.sh`**
  Responsible for compilation. It clones the GCSFuse repository and builds the binary from specific commits. It implements caching to avoid rebuilding the same commit multiple times.

* **`monitor.sh`**
  Captures system metrics. It runs in the background during tests to track CPU, Memory, Network I/O, and Page Cache usage. It also contains the logic for calculating statistical averages and peaks.

* **`runner.sh`**
  The core test logic. It parses test parameters from CSV, generates FIO job files using `envsubst`, runs the FIO iterations, and aggregates results.

## Usage

Edit `run.sh` with your configuration and run:

```bash
./run.sh
```

### Testing Multiple Configurations

Test multiple GCSFuse commits/configurations in a single benchmark run:

**Configuration in `run.sh`**:

```bash
CONFIGS_CSV="configs.csv"  # Enable multi-config mode
SEPARATE_CONFIGS=false     # true = separate CSV per config, false = combined report
```

**`configs.csv` format**:

```csv
commit,mount_args,label
master,"--implicit-dirs --stat-cache-max-size-mb=-1",baseline
main,"--implicit-dirs --enable-kernel-reader=false",no_kernel_reader
feature-branch,"--implicit-dirs --type-cache-max-size-mb=100",new_feature
```

In this mode:

* System generates cartesian product: `configs` × `test_cases`
* Distributes all combinations across available VMs
* Each VM may test different configs

**Report options**:

* **Combined** (`SEPARATE_CONFIGS=false`): Single CSV with Config/Commit/Mount Args columns
* **Separate** (`SEPARATE_CONFIGS=true`): One CSV per config for easy comparison

### Direct Orchestrator Usage

The script passes all parameters as CLI arguments to the orchestrator.

```bash
python3 orchestrator.py \
    --benchmark-id "benchmark-123" \
    --target "my-instance-group-or-vm" \
    --zone "us-west4-a" \
    --project "my-project" \
    --artifacts-bucket "artifacts" \
    --test-csv "tests.csv" \
    --fio-job-file "job.fio" \
    --bucket "test-bucket" \
    --iterations 5 \
    --configs-csv "configs.csv" \
    --separate-configs  # Optional: generate separate reports
```

Results saved to: `results/benchmark-123/`

## Test Configuration

### Test CSV Format

```csv
block_size,file_size,io_depth,io_type,num_jobs,nr_files
4k,1m,1,read,1,1
1m,100m,1,read,1,1
```

### FIO Job Template

Create `jobfile.fio` with bash variable syntax:

```ini
[global]
ioengine=libaio
direct=0
verify=0
bs=$BS
iodepth=$IO_DEPTH
nrfiles=$NRFILES
group_reporting=1

[test]
rw=$IO_TYPE
filesize=$FILE_SIZE
directory=$TEST_DATA_DIR
numjobs=$THREADS
```

Variables: `$BS`, `$FILE_SIZE`, `$IO_DEPTH`, `$IO_TYPE`, `$THREADS`, `$NRFILES`, `$TEST_DATA_DIR`

### Config JSON (Internal)

The orchestrator automatically creates `config.json` from CLI parameters and uploads it to GCS for worker coordination:

```json
{
  "iterations": 5,
  "bucket": "my-test-bucket",
  "separate_configs": false
}
```

Workers download this file to get test execution parameters.

## Workflow

1. **Initialization**: `worker.sh` sets up a workspace in `/tmp/benchmark-<ID>` and redirects all `stdout`/`stderr` to a log file.
2. **Setup**: `setup.sh` runs `apt-get update` (once) and installs any missing tools (`fio`, `git`, `go`, `jq`, `bc`, `gettext-base`).
3. **Configuration**: The worker downloads the job specification (`job.json`), global config, and test cases from the `ARTIFACTS_BUCKET`.
4. **Execution Loop**:
    * **Build**: `build.sh` compiles the required version of GCSFuse.
    * **Mount**: GCSFuse is mounted with the specific flags defined in the job.
    * **Monitor**: `monitor.sh` starts a background process to record resource usage.
    * **Run**: `runner.sh` executes the FIO benchmark.
    * **Teardown**: The system unmounts GCSFuse and cleans up processes.
5. **Reporting**: Results are uploaded to `gs://<ARTIFACTS_BUCKET>/<BENCHMARK_ID>/results/<VM_NAME>`.


## Dependencies

The `setup.sh` script automatically handles the installation of these dependencies:

* **Core**: `git`, `curl`, `jq`, `bc`
* **Templating**: `gettext-base` (provides `envsubst`)
* **Benchmarking**: `fio` (builds from source if necessary for extended latency buckets)
* **Build Tools**: `golang`, `build-essential`, `libaio-dev`

## Available Metrics

The following metrics are captured and reported for each test run:

| Metric | Description |
| :--- | :--- |
| **read_bw** | Read throughput (MB/s) |
| **write_bw** | Write throughput (MB/s) |
| **avg_cpu** | Average GCSFuse CPU usage (%) |
| **peak_cpu** | Peak GCSFuse CPU usage (%) |
| **avg_mem** | Average GCSFuse memory (MB) |
| **peak_mem** | Peak GCSFuse memory (MB) |
| **avg_page_cache** | Average page cache (GB) |
| **peak_page_cache** | Peak page cache (GB) |
| **avg_sys_cpu** | Average system CPU usage (%) |
| **peak_sys_cpu** | Peak system CPU usage (%) |
| **read_lat_p50_ms** | Read P50 latency (ms) |
| **read_lat_p90_ms** | Read P90 latency (ms) |
| **read_lat_p99_ms** | Read P99 latency (ms) |
| **read_lat_max_ms** | Read max latency (ms) |