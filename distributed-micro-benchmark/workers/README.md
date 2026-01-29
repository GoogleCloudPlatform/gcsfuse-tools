# Distributed Micro-Benchmarking Worker

This repository contains the worker scripts for a distributed micro-benchmarking system designed to test **GCSFuse** performance and behavior under various configurations. The monolithic worker has been refactored into a modular architecture for improved maintainability, readability, and debugging.

## 📂 Architecture

The codebase is split into five distinct modules:
 .
 
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

## 🚀 Usage

The worker is typically executed via a startup script on a Google Compute Engine VM, or manually for debugging.

```bash
./worker.sh <BENCHMARK_ID> <ARTIFACTS_BUCKET>
```

### Arguments

* **`BENCHMARK_ID`**: A unique identifier for the current benchmark run (used for pathing in GCS).
* **`ARTIFACTS_BUCKET`**: The GCS bucket name where configuration files are read from and results are written to.

## 🔄 Workflow

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

## 📦 Output Structure

Results are stored in the artifacts bucket under the specific benchmark ID and VM name:

```text
gs://<BUCKET>/<BENCHMARK_ID>/results/<VM_NAME>/
├── manifest.json        # Summary of all tests, status, and high-level metrics
└── test-<TEST_ID>/      # Directory for individual test data
    ├── fio_output_*.json    # Raw JSON output from FIO
    ├── monitor.log          # Time-series CSV of resource usage
    └── gcsfuse_mount_*.log  # Debug logs from GCSFuse
```

## 🛠️ Dependencies

The `setup.sh` script automatically handles the installation of these dependencies:

* **Core**: `git`, `curl`, `jq`, `bc`
* **Templating**: `gettext-base` (provides `envsubst`)
* **Benchmarking**: `fio` (builds from source if necessary for extended latency buckets)
* **Build Tools**: `golang`, `build-essential`, `libaio-dev`