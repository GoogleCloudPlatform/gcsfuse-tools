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
POLL_INTERVAL=30
TIMEOUT=7200
```

The script passes all parameters as CLI arguments to the orchestrator.

**Direct orchestrator usage:**
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

```
gs://artifacts-bucket/benchmark-id/
├── config.json              # Benchmark configuration
├── test-cases.csv           # All test cases
├── jobfile.fio              # FIO job template
├── jobs/                    # Job specs per VM
│   ├── vm-1.json
│   └── vm-2.json
└── results/                 # Results per VM
    ├── vm-1/
    │   ├── manifest.json
    │   └── test-1/
    └── vm-2/
        ├── manifest.json
        └── test-3/
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
