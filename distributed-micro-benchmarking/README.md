# Distributed Micro-Benchmarking

Distributed benchmarking system for GCSFuse across multiple VMs.

## Architecture

- **Orchestrator** (`orchestrator.py`) - Coordinates benchmark execution, distributes test cases, monitors progress, aggregates results
- **Worker** (`resources/worker.sh`) - Runs on each VM, executes assigned tests, uploads results
- **Coordination** - GCS-based with job files and manifest tracking

## Usage

Edit [run.sh](run.sh) with your configuration and run:

```bash
./run.sh
```

### Single-Config Mode (Default)

Test a single GCSFuse commit with specific mount arguments:

**Configuration in run.sh:**
```bash
BENCHMARK_ID="benchmark-$(date +%s)"
INSTANCE_GROUP="my-instance-group"
ZONE="us-west4-a"
PROJECT="my-gcp-project"
ARTIFACTS_BUCKET="my-artifacts-bucket"
TEST_CSV="sample-tests.csv"
FIO_JOB_FILE="jobfile.fio"
BUCKET="my-test-bucket"
ITERATIONS=5
GCSFUSE_COMMIT="master"
GCSFUSE_MOUNT_ARGS="--implicit-dirs"
CONFIGS_CSV=""  # Leave empty for single-config mode
POLL_INTERVAL=30
TIMEOUT=7200
```

### Multi-Config Mode

Test multiple GCSFuse commits/configurations in a single benchmark run:

**Configuration in run.sh:**
```bash
CONFIGS_CSV="configs.csv"  # Enable multi-config mode
SEPARATE_CONFIGS=false     # true = separate CSV per config, false = combined report
```

**configs.csv format:**
```csv
commit,mount_args,label
master,"--implicit-dirs --stat-cache-max-size-mb=-1",baseline
main,"--implicit-dirs --enable-kernel-reader=false",no_kernel_reader
feature-branch,"--implicit-dirs --type-cache-max-size-mb=100",new_feature
```

In multi-config mode:
- System generates cartesian product: configs × test_cases
- Distributes all combinations across available VMs
- Each VM may test different configs
- Report options:
  - **Combined** (`SEPARATE_CONFIGS=false`): Single CSV with Config/Commit/Mount Args columns
  - **Separate** (`SEPARATE_CONFIGS=true`): One CSV per config for easy comparison

The script passes all parameters as CLI arguments to the orchestrator.

**Direct orchestrator usage (single-config):**
```bash
python3 orchestrator.py \
    --benchmark-id "benchmark-123" \
    --instance-group "my-group" \
    --zone "us-west4-a" \
    --project "my-project" \
    --artifacts-bucket "artifacts" \
    --test-csv "tests.csv" \
    --fio-job-file "job.fio" \
    --bucket "test-bucket" \
    --iterations 5 \
    --gcsfuse-commit "master" \
    --gcsfuse-mount-args "--implicit-dirs"
```

**Direct orchestrator usage (multi-config):**
```bash
python3 orchestrator.py \
    --benchmark-id "benchmark-123" \
    --instance-group "my-group" \
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

**Variables:**
- `$BS`, `$FILE_SIZE`, `$IO_DEPTH`, `$IO_TYPE`, `$THREADS`, `$NRFILES`, `$TEST_DATA_DIR`

### Config JSON (Internal)

The orchestrator automatically creates `config.json` from CLI parameters and uploads it to GCS for worker coordination:

```json
{
  "gcsfuse_commit": "master",
  "iterations": 5,
  "bucket": "my-test-bucket",
  "gcsfuse_mount_args": "--implicit-dirs --stat-cache-ttl 60s"
}
```

Workers download this file to get test execution parameters.

## GCS Directory Structure

### Single-Config Mode
```
gs://artifacts-bucket/benchmark-id/
├── config.json              # Benchmark configuration with mode=single-config
├── test-cases.csv           # All test cases
├── jobfile.fio              # FIO job template
├── jobs/                    # Job specs per VM
│   ├── vm-1.json           # Contains: test_ids array
│   └── vm-2.json
└── results/                 # Results per VM
    ├── vm-1/
    │   ├── manifest.json
    │   └── test-1/
    └── vm-2/
        ├── manifest.json
        └── test-3/
```

### Multi-Config Mode
```
gs://artifacts-bucket/benchmark-id/
├── config.json              # Benchmark configuration with mode=multi-config
├── configs.csv              # Config specifications (commit, mount_args, label)
├── test-cases.csv           # All test cases
├── jobfile.fio              # FIO job template
├── jobs/                    # Job specs per VM
│   ├── vm-1.json           # Contains: test_entries array with config per entry
│   └── vm-2.json
└── results/                 # Results per VM
    ├── vm-1/
    │   ├── manifest.json   # Contains matrix_id, config_id metadata
    │   └── test-0/         # Matrix entry: test-0 = config-0 × test-case-0
    └── vm-2/
        ├── manifest.json
        └── test-5/         # Matrix entry: test-5 = config-1 × test-case-2
```

## Components

- `orchestrator.py` - Main coordinator
- `helpers/gcs.py` - GCS operations
- `helpers/vm_manager.py` - VM coordination  
- `helpers/job_generator.py` - Job distribution
- `helpers/result_aggregator.py` - Result parsing
- `helpers/report_generator.py` - Report generation
- `resources/worker.sh` - VM worker script
- `run.sh` - Launcher script
- `test_aggregation.py` - Test result aggregation independently
- `plot_reports.py` - Visualization and plotting

## Plotting Results

The `plot_reports.py` script automatically detects the input type and generates appropriate plots.

### Basic Usage

```bash
# Plot from a single CSV file (auto-detects if it has Config column)
python3 plot_reports.py results/benchmark-123_report.csv

# Plot from a directory of CSV files (combined mode)
python3 plot_reports.py good_reports/

# Specify output file location
python3 plot_reports.py results/benchmark-123_report.csv --output-file my_plots.png

# Plot specific metrics only
python3 plot_reports.py results/benchmark-123_report.csv \
    --metric read_bw avg_cpu peak_cpu
```

### X-Axis Switching

By default, test-cases are on the x-axis and configs are shown as different lines. You can switch this:

```bash
# Default: test-cases on x-axis, configs as different lines
python3 plot_reports.py results/benchmark-123_report.csv

# Switch: configs on x-axis, test-cases as different lines
python3 plot_reports.py results/benchmark-123_report.csv --x-axis configs
```

This is useful for:
- `--x-axis test-cases`: Compare how different configs perform across test cases
- `--x-axis configs`: Compare how different test cases perform across configs

### Mode Selection

**Auto Mode (Default):**
- Single CSV with 'Config' column → Generates separate plots per config
- Single CSV without 'Config' column → Single combined plot
- Directory → Combined plot with all CSV files

**Force Combined Mode:**
```bash
# Plot all configs on same graph (from directory)
python3 plot_reports.py good_reports/ --mode combined
```

**Force Per-Config Mode:**
```bash
# Generate separate plots for each config (requires CSV with Config column)
python3 plot_reports.py results/benchmark-123_report.csv --mode per-config
```

**Per-Config Mode Output:**
When using per-config mode with a multi-config CSV, separate graph files are generated:
```
results/throughput_comparison_gcsfuse_master.png
results/throughput_comparison_gcsfuse_ra32mb.png
results/throughput_comparison_system_cp.png
```

**Available Metrics:**
- `read_bw` - Read throughput (MB/s)
- `write_bw` - Write throughput (MB/s)
- `avg_cpu` - Average GCSFuse CPU usage (%)
- `peak_cpu` - Peak GCSFuse CPU usage (%)
- `avg_mem` - Average GCSFuse memory (MB)
- `peak_mem` - Peak GCSFuse memory (MB)
- `avg_page_cache` - Average page cache (GB)
- `peak_page_cache` - Peak page cache (GB)
- `avg_sys_cpu` - Average system CPU usage (%)
- `peak_sys_cpu` - Peak system CPU usage (%)

**Per-Config Mode Features:**
- Each config gets its own graph file
- Test cases are on the x-axis (sorted by IO type, threads, file size)
- Multiple metrics shown as subplots
- Ideal for comparing test case performance within a single config
- Works with multi-config CSV reports that have a 'Config' column
