# GCS Coherency Validation Tool - User Manual

This tool facilitates comprehensive validation of `gcsfuse` consistency,
coherency, and caching behaviors. It supports various testing workflows,
including single-node testing and a distributed dual-node testing workflow.

## Table of Contents

1.  [Architecture & Buckets](#architecture--buckets)
2.  [Quick Start: Infrastructure Provisioning](#quick-start-infrastructure-provisioning)
3.  [Prerequisites & Setup](#prerequisites--setup)
4.  [Workflows Overview](#workflows-overview)
5.  [Getting Started](#getting-started)
6.  [Workflow 1: single_node_single_mount](#workflow-1-single_node_single_mount)
7.  [Workflow 2: single_node_dual_mounts](#workflow-2-single_node_dual_mounts)
8.  [Workflow 3: dual_node_mounts](#workflow-3-dual_node_mounts)
9.  [Scenario Management & Aliases](#scenario-management--aliases)
10. [File System Operations Reference](#file-system-operations-reference)
11. [Go Tools Reference](#go-tools-reference)
12. [Asynchronous & Interactive Operations](#asynchronous--interactive-operations)
13. [Configuration & Environment Control](#configuration--environment-control)
14. [Logging & Debugging](#logging--debugging)
15. [Troubleshooting](#troubleshooting)

--------------------------------------------------------------------------------

## Architecture & Buckets

This tool requires **Two Distinct GCS Buckets**:

### 1. Shared Code & State Bucket (`SHARED_BUCKET`)

*   **Purpose:** Stores the tool's source code, shared configuration files, and
    the logs for dual-node tests.
*   **Mount Point:** Must be mounted at `$HOME/work/shared` on **all**
    participating VMs.
*   **Permissions:** All test VMs need Read/Write access.
*   **Example Name:** `<user>-coherency-work-shared-bucket-<region>` (e.g.,
    `gargnitin-coherency-work-shared-bucket-asiase1`).

### 2. Test Target Bucket (`TEST_BUCKET`)

*   **Purpose:** The actual bucket being tested for consistency. The tool will
    automatically mount and unmount this bucket during tests.
*   **Mount Point:** The tool creates mount points at
    `$HOME/work/test_buckets/<bucket>-mountX`.
*   **Configuration:** You must update the tool's config files with this
    bucket's name.
*   **Example Name:** `<user>-test-hns-<region>` (e.g.,
    `gargnitin-test-hns-asiase1`).

--------------------------------------------------------------------------------

## Quick Start: Infrastructure Provisioning

If you do not have buckets or VMs yet, use these commands (requires `gcloud`).

### 1. Create Buckets

Replace `<REGION>` (e.g., `asia-southeast1`) and bucket names. **Note:** Buckets
are created with **Hierarchical Namespace** and **Uniform Bucket-Level Access**
enabled.

```bash
# Shared Bucket (Infrastructure)
gcloud storage buckets create gs://<SHARED_BUCKET_NAME> \
    --location=<REGION> \
    --enable-hierarchical-namespace \
    --uniform-bucket-level-access

# Test Target Bucket (The one under test)
gcloud storage buckets create gs://<TEST_BUCKET_NAME> \
    --location=<REGION> \
    --enable-hierarchical-namespace \
    --uniform-bucket-level-access
```

### 2. Create VMs

Create two VMs (Leader/VM1 and Follower/VM2). **Specs:** * **OS:** Ubuntu 25.04
(via image-family `ubuntu-2504-amd64`). * **Disk:** 40GB Boot Disk. *
**Access:** Full Cloud Platform scope (required for GCS Fuse and management).

Replace `<USER>` and `<REGION>` (e.g., `gargnitin`, `asiase1`).

```bash
# VM1 (Leader)
gcloud compute instances create ${USER}-vm1-leader-${REGION} \
    --zone=<ZONE_1> \
    --machine-type=e2-standard-8 \
    --image-family=ubuntu-2504-amd64 --image-project=ubuntu-os-cloud \
    --boot-disk-size=40GB \
    --scopes=https://www.googleapis.com/auth/cloud-platform

# VM2 (Follower)
gcloud compute instances create ${USER}-vm2-follower-${REGION} \
    --zone=<ZONE_2> \
    --machine-type=e2-standard-8 \
    --image-family=ubuntu-2504-amd64 --image-project=ubuntu-os-cloud \
    --boot-disk-size=40GB \
    --scopes=https://www.googleapis.com/auth/cloud-platform
```

--------------------------------------------------------------------------------

## Prerequisites & Setup

### 1. VM Provisioning

*   **Single Node Workflows:** Require 1 VM.
*   **Dual Node Workflow:** Requires **2 VMs** (referred to as VM1/Leader and
    VM2/Follower).
*   **OS:** Linux (Ubuntu/Debian recommended).

### 2. Software Installation (On All VMs)

You must install Go, GCS Fuse, and Python on all VMs involved in the testing.

**a. Install Go (Version 1.24.10)** The tool uses Go for direct I/O operations
(`write.go`, `read.go`). ```bash

# Remove any existing installation

sudo rm -rf /usr/local/go

# Download and install (Adjust OS/Arch if not linux-amd64)

# Note: Ensure version 1.24.10 exists; if not, use the latest stable (e.g., 1.22.x)

wget https://go.dev/dl/go1.24.10.linux-amd64.tar.gz sudo tar -C /usr/local -xzf
go1.24.10.linux-amd64.tar.gz

# Add to PATH (Add this to your ~/.bashrc for persistence)

echo "export PATH=\$PATH:/usr/local/go/bin" >> ~/.bashrc

source ~/.bashrc



# Verify

go version

```

**b. Install GCS Fuse (Latest)**
Follow the official [GCS Fuse Installation Guide](https://cloud.google.com/storage/docs/cloud-storage-fuse/install).

**Option 1: Standard Installation (Ubuntu 24.04 and older)**
```bash
export GCSFUSE_REPO=gcsfuse-`lsb_release -c -s`
echo "deb https://packages.cloud.google.com/apt $GCSFUSE_REPO main" | sudo tee /etc/apt/sources.list.d/gcsfuse.list
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key add -
sudo apt-get update
sudo apt-get install -y gcsfuse
```

**Option 2: Modern/Ubuntu 25.04+ (Keyring Method)**
Use this if you encounter "NO_PUBKEY" errors or are running Ubuntu 25.04+.
```bash
# 1. Add the public key to the system keyring (Dearmor ensures binary format)
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/gcsfuse-keyring.gpg

# 2. Add the repo (forcing 'noble' codename for stability on newer releases)
echo "deb [signed-by=/usr/share/keyrings/gcsfuse-keyring.gpg] https://packages.cloud.google.com/apt gcsfuse-noble main" | sudo tee /etc/apt/sources.list.d/gcsfuse.list

# 3. Update and install
sudo apt-get update
sudo apt-get install -y gcsfuse
```

**c. Install Python System Dependencies** The tool requires Python 3. Install
the system-level dependencies first.

```bash
# 1. Install System Python Dependencies
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git
```

### 3. Configure Hostnames (For Dual Node)

If running the `dual_node_mounts` workflow, the tool needs to know which VM is
"Mount 1" and which is "Mount 2". * Edit `dual_node_mounts/config.py` (after
you've populated the shared bucket, see Step 4). * Update the logic mapping
`HOSTNAME` to `MOUNT_NUMBER`.

### 4. Setup the Shared Bucket (One-time Setup)

You need to populate your `SHARED_BUCKET` with the tool code.

**On one VM (e.g., VM1):**

1.  **Mount the empty shared bucket:** ```bash mkdir -p $HOME/work/shared

    # Replace <YOUR_SHARED_BUCKET_NAME> with your actual bucket name

    gcsfuse --implicit-dirs <YOUR_SHARED_BUCKET_NAME> $HOME/work/shared ```

2.  **Download and install the tool into the bucket:** Run the following block
    to clone the repo and copy the validation tools into the mounted bucket.
    ```bash cd /tmp git clone
    https://github.com/GoogleCloudPlatform/gcsfuse-tools.git

    # Copy the python tools to the shared mount if not already present

    if [ ! -d "$HOME/work/shared/coherency-validation" ]; then mkdir -p
    $HOME/work/shared/coherency-validation # Adjust path if repo structure
    differs, targeting the 'python' folder cp -rf
    gcsfuse-tools/coherence-validation/python
    $HOME/work/shared/coherency-validation/ echo "Tool code deployed to shared
    bucket." else echo "Tool code already exists in shared bucket." fi

    rm -rf /tmp/gcsfuse-tools ```

**On the other VM (VM2):** Simply mount the bucket to access the code deployed
by VM1. `bash mkdir -p $HOME/work/shared gcsfuse --implicit-dirs
<YOUR_SHARED_BUCKET_NAME> $HOME/work/shared`

### 5. Setup Python Virtual Environment (On All VMs)

Now that the code is present in the shared mount, set up the virtual
environment.

```bash
# 1. Navigate to the tool directory in the shared mount
cd $HOME/work/shared/coherency-validation/python

# 2. Setup the Virtual Environment
./setup_venv.sh

# 3. Activate the Environment (Required before running tests)
source ~/.cache/coherency-validation/.venv/bin/activate
```

### 6. Create Workspace Directories

Create the following directories on **all** VMs (local state): `bash mkdir -p
$HOME/work/tasks mkdir -p $HOME/work/test_buckets`

### 7. Configure the Test Bucket Name

You must tell the tool which bucket to use for the actual testing. * Open
`$HOME/work/shared/coherency-validation/python/dual_node_mounts/config.py`. *
Locate the variable `BUCKET_NAME`. * Change it to your **Test Target Bucket**
(e.g., `<user>-test-hns-asiase1`). `python # dual_node_mounts/config.py
BUCKET_NAME = "my-test-bucket-name"` * *Repeat for
`single_node_dual_mounts/config.py` and `single_node_single_mount/config.py` if
running those workflows.*

--------------------------------------------------------------------------------

## Workflows Overview

Workflow                     | Description           | Use Case                                                    | Log Location
:--------------------------- | :-------------------- | :---------------------------------------------------------- | :-----------
**single_node_single_mount** | 1 VM, 1 Mount point.  | Basic sanity checks, concurrent reads/writes on same mount. | `~/work/tasks/...`
**single_node_dual_mounts**  | 1 VM, 2 Mount points. | Local coherency (does Mount 2 see Mount 1's changes?).      | `~/work/tasks/...`
**dual_node_mounts**         | 2 VMs, 1 Mount each.  | Distributed coherency (does VM 2 see VM 1's changes?).      | `~/work/shared/...` (Preserved in Shared Bucket)

--------------------------------------------------------------------------------

## Getting Started

1.  **Navigate to the tool directory (in the shared mount):** `bash cd
    $HOME/work/shared/coherency-validation/python`

2.  **Source the aliases:** `bash source workflow_aliases.sh`

3.  **Select your workflow:** ```bash set_workflow

    # Select 1, 2, or 3 from the menu.

    ```
    *This loads the environment variables and aliases specific to that workflow.*
    ```

--------------------------------------------------------------------------------

## Workflow 1: single_node_single_mount

**Execution:** Automated tests on a single mount point. Logs are stored locally
in `~/work/tasks`.

*   **List Scenarios:** `execute_scenario --list`
*   **Run (Step Mode):** `execute_scenario <ID>`
*   **Run (Auto Mode):** `execute_scenario_complete <ID>`

**Supported Scenarios:** * Basic CRUD, Symlinks, Sync/Flush testing. *
**Concurrency:** Reading/Writing large files from multiple threads (e.g.,
Scenario 25, 26).

--------------------------------------------------------------------------------

## Workflow 2: single_node_dual_mounts

**Execution:** Mounts the **Test Bucket** twice locally (Mount 1 & Mount 2).
Logs are stored locally.

*   **List Scenarios:** `execute_scenario --list`
*   **Run (Step Mode):** `execute_scenario <ID>` (Follow printed instructions).
*   **Run (Auto Mode):** `execute_scenario_complete <ID>`

--------------------------------------------------------------------------------

## Workflow 3: dual_node_mounts

**Execution:** Coordinated testing between VM1 (Leader) and VM2 (Follower). Logs
are stored in the **Shared Bucket**, so they are automatically preserved even if
VMs are deleted.

**Important Logic:** * **Shared Log:** Both VMs write to the same log file in
the shared bucket. * **Safety Sleeps:** To ensure metadata propagation across
the shared bucket (which relies on `gcsfuse`), the tool enforces a configurable
sleep (default **15s**) after writing to shared state files.

**Steps:**

1.  **On VM1 (Leader):** ```bash execute_scenario <ID>

    # Example: execute_scenario 13

    ```
    ```

    *   Initializes the test config in the shared bucket.
    *   Resets VM1's mount of the **Test Bucket**.
    *   Prints instructions.

2.  **On VM2 (Follower):** ```bash execute_scenario

    # No ID required.

    ```
    ```

    *   Detects the active scenario from the shared config.
    *   Resets VM2's mount of the **Test Bucket**.
    *   Prints instructions.

3.  **Completion:**

    *   Run `complete_scenario` (on either VM) to clean up.

--------------------------------------------------------------------------------

## Scenario Management & Aliases

These high-level aliases manage the lifecycle of a test scenario.

*   `execute_scenario [ID]`: Starts a scenario (in step mode) or joins an
    existing one. Resets mounts and prepares the environment.
*   `complete_scenario` / `mark_scenario_completed`: Marks the current scenario
    as successfully finished. Cleans up temporary state files and finalizes the
    log. **Must be run at the end of every scenario.**
*   `abort_scenario` / `abort_current_scenario`: Forcibly stops the current
    scenario without marking it as success. Useful if a test hangs or you want
    to restart.
*   `fail_scenario`: Explicitly marks the scenario as FAILED in the log and
    cleans up.

--------------------------------------------------------------------------------

## File System Operations Reference

These aliases run the actual file system tests. They often have assertions
built-in (e.g., `readfileandfail` expects the read to fail).

**IMPORTANT:** These operations should be run **only** after starting a scenario
(`execute_scenario`) and from **inside** the mounted directory. The tool
typically switches your working directory to the mount automatically, but you
should verify you are in a path like `.../test_buckets/<bucket>-mountX` before
running them.

**Basic File Operations** * `createfile`: Creates `sample.txt` with default
content. * `createfilewith2ndcontent`: Creates `sample.txt` with
"sample_content2". * `create2ndfile`: Creates `sample2.txt`. * `readfile`: Reads
`sample.txt` and prints content. * `readfilehasoriginalcontent`: Reads
`sample.txt` and asserts it contains "sample_content". *
`readfilehasupdatedcontent`: Reads `sample.txt` and asserts it contains
"sample_content2". * `updatefile`: Overwrites `sample.txt` with new content. *
`deletefile`: Deletes `sample.txt`. * `listfile`: Checks if `sample.txt` exists
(via `ls`). * `renamefile`: Renames `sample.txt` to `sample2.txt`.

**Negative Testing (Expect Failure)** * `readfileandfail`: Tries to read
`sample.txt`, succeeds if the read FAILS. * `listfileandfail`: Tries to list
`sample.txt`, succeeds if it does NOT exist. * `read2ndfileandfail`: Tries to
read `sample2.txt`, succeeds if it FAILS.

**Directory Operations** * `createdir`: Creates `sample_dir`. * `listdir`:
Checks if `sample_dir` exists. * `deletedir`: Deletes `sample_dir`. *
`renamedir`: Renames `sample_dir` to `sample_dir2`. * `listdirandfail`: Checks
if `sample_dir` does NOT exist.

**Symlink Operations** * `createsymlink`: Creates `sample.lnk` pointing to
`sample.txt`. * `listsymlink`: Checks if the symlink exists. *
`readfromsymlink`: Reads the target content via the symlink. * `deletesymlink`:
Deletes the symlink. * `listsymlinkandfail`: Checks if symlink is gone.

**Advanced I/O (Go-based)** * `writedirectfile`: Writes using `O_DIRECT`. *
`readdirectfile`: Reads using `O_DIRECT`. * `writefilewithoutsync`: Writes
without calling `fsync()`. * `writefilewithoutflush`: Writes without calling
`close()` (or flush), holding the handle open. * `writebigfile`: Writes a large
(2GB) file. * `writebigfileconcurrently`: Spawns multiple threads to write to
the same large file simultaneously (stress test).

--------------------------------------------------------------------------------

## Go Tools Reference

The python framework relies on compiled Go programs for operations that require
precise control over system calls (Direct I/O, Flush control, Threading) which
are difficult to achieve in pure Python.

### `write.go`

A robust file writing utility with low-level flags. * **Usage:** `go run
write.go [flags] <filepath>` * **Flags:** * `--content <str>`: String content to
write. * `--size <str>`: File size to generate (e.g., "1G", "10M"). Overrides
content. * `--direct`: Uses `O_DIRECT` (bypasses kernel page cache). Writes are
aligned to 4096 bytes. * `--no-sync`: Skips `file.Sync()` (fsync). *
`--no-flush`: Skips `file.Close()`. **Blocks execution** until interrupted
(Ctrl+C). Used to simulate open handles. * `--duplicate-writes <N>`: Spawns `N`
concurrent threads writing the same content to the same file. Used to test race
conditions.

### `read.go`

A simple file reader that supports Direct I/O. * **Usage:** `go run read.go
[flags] <filepath>` * **Flags:** * `--direct`: Uses `O_DIRECT`.

### `read_concurrently.go`

High-conformance threaded reader for stress testing. * **Usage:** `go run
read_concurrently.go [flags] <filepath>` * **Flags:** * `--size <str>`: Expected
file size (verifies file is not truncated). * `--threads <N>`: Number of
concurrent read threads. * `--verify`: Verifies content matches the
deterministic pattern generated by `write.go`. * `--direct`: Uses `O_DIRECT`.

--------------------------------------------------------------------------------

## Asynchronous & Interactive Operations

### Asynchronous Operations

Some operations, particularly those involving large file writes in stress tests,
run in the background.

*   **`writebigfileasync`**: Starts writing a large file (2GB) in the
    background.
*   **`writedirectbigfileasync`**: Starts writing a large file with `O_DIRECT`
    in the background.
*   **`waitforbackgroundjobs`**: Blocks until all currently running background
    jobs (started by the above commands) have finished.

**Usage Pattern:** ```bash

# Start simultaneous writes (e.g., in a dual-node scenario)

mount1 writebigfileasync & # Start job 1 mount2 writebigfileasync & # Start job
2 waitforbackgroundjobs # Wait for both to finish ```

### Interactive / Blocking Operations

These operations intentionally block execution to simulate specific file handle
states (e.g., holding a file open without flushing). **You must manually
interrupt them.**

*   **`writefilewithoutflush`**: Writes data but does NOT close the file
    descriptor. It hangs indefinitely to keep the handle open.
*   **`writedirectfilewithoutflush`**: Same as above, but with `O_DIRECT`.
*   **`writefilewithoutsyncorflush`**: Same as above, but also skips `fsync()`.

**Usage Pattern:** 1. Run the command: `writefilewithoutflush` 2. The terminal
will show: `>> Waiting for interrupt signal (Ctrl+C) to exit...` 3. Perform your
check (e.g., verify file visibility from another mount). 4. Press **Ctrl+C** to
interrupt the process, forcing it to close the handle and (usually) flush data.

--------------------------------------------------------------------------------

## Configuration & Environment Control

You can inspect and modify the environment using these aliases:

### Runtime Settings

*   **`set_sleep_seconds <N>`**: Sets the duration (in seconds) the tool waits
    after writing to a shared file (e.g., the shared log or config). Default is
    15s. Increase this if you see "No scenario running" errors due to slow GCS
    Fuse metadata propagation. `bash set_sleep_seconds 30`
*   **`enable_logging`**: Enables logging of command output to the log file.
*   **`disable_logging`**: Disables **ALL** logging to the file. No commands,
    headers, or output will be written to the shared log. Useful for extreme
    latency sensitivity testing where even log I/O is undesirable.

### Status & Inspection

*   **`current_config`**: Prints the content of the global workflow
    configuration (JSON).
*   **`current_logfile`**: Prints the absolute path to the log file currently
    being used.
*   **`current_scenario`**: Prints the name of the currently active scenario.
*   **`current_mount`**: Prints whether the current shell is configured as Mount
    1 or Mount 2 (based on hostname).

--------------------------------------------------------------------------------

## Logging & Debugging

**Log Persistence:** * **Dual Node:** Logs are safe in the `SHARED_BUCKET`. *
**Single Node:** Logs are in `~/work/tasks` (Local SSD). **You must manually
copy these to the shared bucket** if you wish to preserve them after deleting
the VM. `bash cp -r
~/work/tasks/coherency-validation/python/single_node_single_mount/exec_log_*.log
~/work/shared/saved_logs/`

**Manual Logging:** `bash log_custom "Observation: File appeared after 3
seconds."`

--------------------------------------------------------------------------------

## Troubleshooting

*   **"No scenario currently running"**:
    *   For `dual_node`, ensure VM1 started the scenario first.
    *   Check if `$HOME/work/shared` is mounted correctly on both VMs.
*   **Git Errors / "Unable to read tree"**:
    *   Avoid running git commands inside the `~/work/shared` mount if it's a
        simple copy. Perform git operations in `/tmp` and copy files over.
*   **Indentation/Syntax Errors**:
    *   Check `execute_scenarios.py`.
