# Distributed Micro-Benchmarking

Clean, redesigned distributed benchmarking system for GCSFuse.

## Architecture

### Orchestrator (`orchestrator.py`)
- Coordinates benchmark execution across multiple VMs
- Distributes test cases evenly
- Monitors progress via manifests
- Aggregates results

### Worker (`resources/worker.sh`)
- Runs on each VM
- Downloads job specification from GCS
- Executes assigned tests
- Uploads results to VM-specific directory
- Writes progress manifest

## Key Improvements

1. **VM-Specific Results**: Each VM writes to its own directory
2. **Immutable Jobs**: Job specs are uploaded once, VMs download them
3. **Manifest-Based Tracking**: Clear status for each VM and test
4. **No Metadata Coupling**: No dependency on VM metadata
5. **Clean Workspace**: Fresh start for each run

## Usage

### Quick Start with run.sh

1. Edit [run.sh](run.sh) with your configuration:
```bash
INSTANCE_GROUP="my-instance-group"
TEST_CSV="sample-tests.csv"
FIO_JOB_FILE="jobfile.fio"
BUCKET="my-test-bucket"
ARTIFACTS_BUCKET="my-artifacts-bucket"
ZONE="us-west4-a"
PROJECT="my-gcp-project"
ITERATIONS=5
GCSFUSE_COMMIT="master"
```

2. Run the benchmark:
```bash
./run.sh
```

### Direct Orchestrator Usage

```bash
python3 orchestrator.py \
    --benchmark-id my-benchmark-001 \
    --instance-group my-vms \
    --test-csv test-cases.csv \
    --fio-job-file jobfile.fio \
    --bucket gs://test-bucket \
    --artifacts-bucket gs://artifacts-bucket \
    --zone us-west4-a \
    --project my-project \
    --iterations 5
```

## Directory Structure

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
    │   ├── test-1/
    │   └── test-2/
    └── vm-2/
        ├── manifest.json
        ├── test-3/
        └── test-4/
```

## FIO Job Files

The system uses FIO job file templates to define I/O workloads. The job file is uploaded to GCS and used by all workers.

### Example FIO Job File

```ini
[global]
ioengine=libaio
direct=1
time_based
runtime=60s
group_reporting

[read-test]
rw=read
bs=BLOCK_SIZE
iodepth=IO_DEPTH
numjobs=NUM_JOBS
directory=MOUNT_POINT
filename=testfile
size=FILE_SIZE
```

### Parameters Replaced by Worker

The worker script replaces placeholders in the FIO job file:
- `BLOCK_SIZE` - Block size from test case (e.g., 4k, 1m)
- `IO_DEPTH` - I/O depth from test case
- `NUM_JOBS` - Number of parallel jobs from test case
- `MOUNT_POINT` - GCSFuse mount point
- `FILE_SIZE` - File size from test case

### Test CSV Format

```csv
test_id,block_size,io_depth,num_jobs,file_size
1,4k,32,1,1g
2,1m,64,4,10g
3,128k,16,2,5g
```

## Components

- `helpers/gcs.py` - GCS operations
- `helpers/vm_manager.py` - VM coordination
- `helpers/job_generator.py` - Job distribution
- `helpers/result_aggregator.py` - Result parsing
- `helpers/report_generator.py` - Report creation
