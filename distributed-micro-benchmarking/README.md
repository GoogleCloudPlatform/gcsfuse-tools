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

Configuration variables:
```bash
INSTANCE_GROUP="my-instance-group"     # GCE instance group name
TEST_CSV="sample-tests.csv"            # Test cases file
FIO_JOB_FILE="jobfile.fio"            # FIO job template
BUCKET="my-test-bucket"               # GCS bucket for testing
ARTIFACTS_BUCKET="my-artifacts-bucket" # GCS bucket for artifacts
ZONE="us-west4-a"                     # GCP zone
PROJECT="my-gcp-project"              # GCP project
ITERATIONS=5                          # Iterations per test
GCSFUSE_COMMIT="master"               # GCSFuse branch/commit
```

## Test Configuration

### Test CSV Format

```csv
block_size,file_size,io_depth,io_type,num_jobs,nr_files
4k,1m,1,read,1,1
1m,100m,1,read,1,1
```

### FIO Job Template

Create `jobfile.fio` with placeholders that get replaced by worker:

```ini
[global]
ioengine=libaio
direct=0
verify=0
bs=BLOCK_SIZE
iodepth=IO_DEPTH
nrfiles=NR_FILES
group_reporting=1

[test]
rw=IO_TYPE
filesize=FILE_SIZE
directory=MOUNT_POINT
numjobs=NUM_JOBS
```

**Placeholders:**
- `BLOCK_SIZE`, `FILE_SIZE`, `IO_DEPTH`, `IO_TYPE`, `NUM_JOBS`, `NR_FILES`, `MOUNT_POINT`

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
