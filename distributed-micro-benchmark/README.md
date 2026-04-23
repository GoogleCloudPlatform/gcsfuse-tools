# Distributed Micro-Benchmark

This directory contains tools to run distributed performance micro-benchmarks for GCSFuse using FIO. It uses multiple Google Compute Engine instances to execute tests in parallel, orchestrating the workload and gathering the results.

## Overview

The main entry point for running published benchmarks is `run.sh`. This script handles environment setup, updating GCSFuse commit hashes in the configuration, uploading worker scripts to Google Cloud Storage, and invoking the Python orchestrator (`orchestrator.py`).

The tests are defined via:
- **Test Cases CSV:** Specifies FIO parameters like block size, file size, thread count, etc.
- **Mount Configs CSV:** Specifies the GCSFuse mount arguments (flags) to test.
- **FIO Job File:** The base FIO configuration file.

The orchestrator will run a matrix of (Test Case x Mount Config) iterations and aggregate the results.

**Note:** `kokoro_run.sh` and other `kokoro_*` suites are meant for automated CI/CD and should generally not be modified for manual runs.

## Usage

You can run the default benchmarks using `run.sh`:

```bash
./run.sh [OPTIONS]
```

### Options

- `--commit <gcsfuse_commit_hash>`: Run the benchmark using a specific GCSFuse commit hash. It defaults to `master`. The script will automatically update the mount configs CSV files to use this commit.
- `--read`: Run only the read benchmarks.
- `--write`: Run only the write benchmarks.

If neither `--read` nor `--write` is specified, both will be run.

### Example

To run both read and write benchmarks for a specific commit:

```bash
./run.sh --commit 3f5b72e9a
```

To run only the read benchmark for the `master` branch:

```bash
./run.sh --read
```

## Customizing the Benchmark

If you want to run custom tests (e.g., testing different FIO parameters, different GCSFuse flags, or custom FIO jobs), you can do so by modifying the variables in `run.sh` or the CSV/FIO files themselves.

### Modifying Test Cases

The test cases define the workload parameters. By default, `run.sh` uses:
- Read: `test_suites/published_benchmarks/read_test_cases.csv`
- Write: `test_suites/published_benchmarks/write_test_cases.csv`

To change the workloads, you can either edit these files directly or create your own CSV files and point `run.sh` to them by changing the `READ_TEST_CSV` and `WRITE_TEST_CSV` variables in the script.

### Modifying GCSFuse Flags (Mount Configs)

To test different GCSFuse flags, you need to modify the mount configs CSV files.

By default, `run.sh` uses:
- Read: `test_suites/published_benchmarks/read_mount_configs.csv`
- Write: `test_suites/published_benchmarks/write_mount_configs.csv`

These CSV files have the following format:

```csv
commit,mount_args,label
master,"--implicit-dirs --client-protocol=grpc",grpc
master,"--implicit-dirs --client-protocol=http1",http1
```

- `commit`: The GCSFuse commit hash. (Note: `run.sh --commit <hash>` automatically overwrites the `commit` column in these files).
- `mount_args`: The flags passed to GCSFuse when mounting the bucket. **Modify this column to add or change flags.**
- `label`: A friendly name for this configuration used in reporting.

You can add new rows to test multiple configurations sequentially.

### Changing FIO Jobs

The FIO job files define the low-level FIO execution parameters.
- Read: `test_suites/published_benchmarks/read.fio`
- Write: `test_suites/published_benchmarks/write.fio`

You can edit these files or change `READ_FIO_JOB_FILE` and `WRITE_FIO_JOB_FILE` in `run.sh` to point to custom `.fio` files.

### Infrastructure Configuration

`run.sh` has several variables at the top of the file that define the Google Cloud infrastructure used for the tests. If you are running this in your own project, you will need to update these:

- `PROJECT`: Your Google Cloud project ID.
- `INSTANCE_GROUP_NAME`: The name of the Managed Instance Group to use as workers.
- `REGIONAL_TEST_DATA_BUCKET`: A pre-existing bucket used for reading/writing test data.
- `ARTIFACTS_BUCKET`: A bucket used to store scripts and intermediate benchmark logs.
- `ZONE`: The Google Cloud zone where the instance group is located.

## Analyzing Results

### Using `analyse.py`
While the benchmarks are running, or after they have completed, you can use `analyse.py` to get a detailed view of the status of all jobs across the worker VMs.

1. Open `analyse.py`.
2. Update the `BUCKET` variable to match your `ARTIFACTS_BUCKET`.
3. Update the `BENCHMARK_ID` variable to match the specific benchmark run you want to inspect.
4. Run the script:

```bash
python3 analyse.py
```

This will output a summary table showing which VMs have completed, failed, or are still running, followed by a detailed breakdown of which specific test configurations were assigned to which VMs, and their individual pass/fail statuses and durations.

### Final Results
Once the benchmarks complete, the results will be saved locally in the `results/` directory as CSV reports (e.g., `results/<benchmark-id>-read/read_combined_report.csv`). If configured correctly, `run.sh` will also attempt to upload these results to BigQuery.

## How it Works

### Execution Order
By default, `run.sh` executes benchmarks sequentially. If both read and write are enabled, it will first complete the entire read benchmark matrix, aggregate the results, and then begin the write benchmark matrix. They do not run concurrently.

### Distribution Example (The Cross Product)
The orchestrator creates a "cross product" of your test cases and your mount configs.

**Example:**
- **Mount Configs (2):** `grpc` and `http1`
- **Read Test Cases (10):** 10 different combinations of FIO parameters (block size, threads, etc.)

This creates a matrix of **2 x 10 = 20 total jobs**.

If your managed instance group is scaled to **4 VMs**, the orchestrator will distribute these 20 jobs evenly among the available VMs. Each VM will receive approximately 5 jobs (e.g., VM 1 gets jobs 1-5, VM 2 gets jobs 6-10, etc.). The VMs will pull their specific assignments from the artifacts bucket and run only their assigned portion of the matrix.

### Artifacts Bucket Hierarchy
The `ARTIFACTS_BUCKET` defined in `run.sh` is used heavily by the orchestrator to communicate with the worker VMs. The structure looks like this:

```
gs://<ARTIFACTS_BUCKET>/
├── scripts/                # The modular worker scripts uploaded by run.sh
└── <BENCHMARK_ID>-read/    # Or -write (The specific benchmark run)
    ├── jobs/               # Contains JSON files. One for each VM, defining the jobs it should run.
    │   ├── <vm-1-name>.json
    │   ├── <vm-2-name>.json
    │   └── ...
    └── results/            # Worker VMs upload their output here as they finish.
        ├── <vm-1-name>/
        │   ├── manifest.json       # VM status (running, completed, failed)
        │   ├── fio_durations.csv   # Aggregated results for the jobs this VM ran
        │   └── logs/               # Raw fio output and GCSFuse logs
        ├── <vm-2-name>/
        └── ...
```
