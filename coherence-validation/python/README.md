# GCS Coherency Validation Tool - User Manual

This tool facilitates comprehensive validation of `gcsfuse` consistency, coherency, and caching behaviors. It supports various testing workflows, including single-node testing and a distributed dual-node testing workflow.

## Table of Contents

1.  [Architecture & Buckets](#architecture--buckets)
2.  [Prerequisites & Setup](#prerequisites--setup)
3.  [Workflows Overview](#workflows-overview)
4.  [Getting Started](#getting-started)
5.  [Workflow 1: single_node_single_mount](#workflow-1-single_node_single_mount)
6.  [Workflow 2: single_node_dual_mounts](#workflow-2-single_node_dual_mounts)
7.  [Workflow 3: dual_node_mounts](#workflow-3-dual_node_mounts)
8.  [Scenario Management & Aliases](#scenario-management--aliases)
9.  [File System Operations Reference](#file-system-operations-reference)
10. [Asynchronous & Interactive Operations](#asynchronous--interactive-operations)
11. [Configuration](#configuration)
12. [Logging & Debugging](#logging--debugging)
13. [Troubleshooting](#troubleshooting)

---

## Architecture & Buckets

This tool requires **Two Distinct GCS Buckets**:

### 1. Shared Code & State Bucket (`SHARED_BUCKET`)
*   **Purpose:** Stores the tool's source code, shared configuration files, and the logs for dual-node tests.
*   **Mount Point:** Must be mounted at `$HOME/work/shared` on **all** participating VMs.
*   **Permissions:** All test VMs need Read/Write access.
*   **Example Name:** `<user>-coherency-work-shared-bucket-<region>` (e.g., `gargnitin-coherency-work-shared-bucket-asiase1`).

### 2. Test Target Bucket (`TEST_BUCKET`)
*   **Purpose:** The actual bucket being tested for consistency. The tool will automatically mount and unmount this bucket during tests.
*   **Mount Point:** The tool creates mount points at `$HOME/work/test_buckets/<bucket>-mountX`.
*   **Configuration:** You must update the tool's config files with this bucket's name.
*   **Example Name:** `<user>-test-hns-<region>` (e.g., `gargnitin-test-hns-asiase1`).

---

## Prerequisites & Setup

### 1. VM Provisioning
*   **Single Node Workflows:** Require 1 VM.
*   **Dual Node Workflow:** Requires **2 VMs** (referred to as VM1/Leader and VM2/Follower).
*   **OS:** Linux (Ubuntu/Debian recommended).
*   **Dependencies:** Python 3, Go (for direct I/O tests), `gcsfuse` installed.

### 2. Configure Hostnames (For Dual Node)
If running the `dual_node_mounts` workflow, the tool needs to know which VM is "Mount 1" and which is "Mount 2".
*   Edit `dual_node_mounts/config.py` (after you've populated the shared bucket, see Step 3).
*   Update the logic mapping `HOSTNAME` to `MOUNT_NUMBER`.

### 3. Setup the Shared Bucket (One-time Setup)
You need to populate your `SHARED_BUCKET` with the tool code.

**On one VM (e.g., VM1):**

1.  **Mount the empty shared bucket:**
    ```bash
    mkdir -p $HOME/work/shared
    # Replace <YOUR_SHARED_BUCKET_NAME> with your actual bucket name
    gcsfuse --implicit-dirs <YOUR_SHARED_BUCKET_NAME> $HOME/work/shared
    ```

2.  **Download and install the tool into the bucket:**
    Run the following block to clone the repo and copy the validation tools into the mounted bucket.
    ```bash
    cd /tmp
    git clone https://github.com/GoogleCloudPlatform/gcsfuse-tools.git
    
    # Copy the python tools to the shared mount if not already present
    if [ ! -d "$HOME/work/shared/coherency-validation" ]; then
        mkdir -p $HOME/work/shared/coherency-validation
        # Adjust path if repo structure differs, targeting the 'python' folder
        cp -rf gcsfuse-tools/coherence-validation/python $HOME/work/shared/coherency-validation/
        echo "Tool code deployed to shared bucket."
    else
        echo "Tool code already exists in shared bucket."
    fi
    
    rm -rf /tmp/gcsfuse-tools
    ```

**On the other VM (VM2):**
Simply mount the bucket to access the code deployed by VM1.
```bash
mkdir -p $HOME/work/shared
gcsfuse --implicit-dirs <YOUR_SHARED_BUCKET_NAME> $HOME/work/shared
```

### 4. Create Workspace Directories
Create the following directories on **all** VMs (local state):
```bash
mkdir -p $HOME/work/tasks
mkdir -p $HOME/work/test_buckets
```

### 5. Configure the Test Bucket Name
You must tell the tool which bucket to use for the actual testing.
*   Open `$HOME/work/shared/coherency-validation/python/dual_node_mounts/config.py`.
*   Locate the variable `BUCKET_NAME`.
*   Change it to your **Test Target Bucket** (e.g., `<user>-test-hns-asiase1`).
    ```python
    # dual_node_mounts/config.py
    BUCKET_NAME = "my-test-bucket-name" 
    ```
*   *Repeat for `single_node_dual_mounts/config.py` and `single_node_single_mount/config.py` if running those workflows.*

---

## Workflows Overview

| Workflow | Description | Use Case | Log Location |
| :--- | :--- | :--- | :--- |
| **single_node_single_mount** | 1 VM, 1 Mount point. | Basic sanity checks, concurrent reads/writes on same mount. | `~/work/tasks/...` |
| **single_node_dual_mounts** | 1 VM, 2 Mount points. | Local coherency (does Mount 2 see Mount 1's changes?). | `~/work/tasks/...` |
| **dual_node_mounts** | 2 VMs, 1 Mount each. | Distributed coherency (does VM 2 see VM 1's changes?). | `~/work/shared/...` (Preserved in Shared Bucket) |

---

## Getting Started

1.  **Navigate to the tool directory (in the shared mount):**
    ```bash
    cd $HOME/work/shared/coherency-validation/python
    ```

2.  **Source the aliases:**
    ```bash
    source workflow_aliases.sh
    ```

3.  **Select your workflow:**
    ```bash
    set_workflow
    # Select 1, 2, or 3 from the menu.
    ```
    *This loads the environment variables and aliases specific to that workflow.*

---

## Workflow 1: single_node_single_mount

**Execution:**
Automated tests on a single mount point. Logs are stored locally in `~/work/tasks`.

*   **List Scenarios:** `execute_scenario --list`
*   **Run (Step Mode):** `execute_scenario <ID>`
*   **Run (Auto Mode):** `execute_scenario_complete <ID>`

**Supported Scenarios:**
*   Basic CRUD, Symlinks, Sync/Flush testing.
*   **Concurrency:** Reading/Writing large files from multiple threads (e.g., Scenario 25, 26).

---

## Workflow 2: single_node_dual_mounts

**Execution:**
Mounts the **Test Bucket** twice locally (Mount 1 & Mount 2). Logs are stored locally.

*   **List Scenarios:** `execute_scenario --list`
*   **Run (Step Mode):** `execute_scenario <ID>` (Follow printed instructions).
*   **Run (Auto Mode):** `execute_scenario_complete <ID>`

---

## Workflow 3: dual_node_mounts

**Execution:**
Coordinated testing between VM1 (Leader) and VM2 (Follower). Logs are stored in the **Shared Bucket**, so they are automatically preserved even if VMs are deleted.

**Important Logic:**
*   **Shared Log:** Both VMs write to the same log file in the shared bucket.
*   **Safety Sleeps:** To ensure metadata propagation across the shared bucket (which relies on `gcsfuse`), the tool enforces a configurable sleep (default **15s**) after writing to shared state files.

**Steps:**

1.  **On VM1 (Leader):**
    ```bash
    execute_scenario <ID>
    # Example: execute_scenario 13
    ```
    *   Initializes the test config in the shared bucket.
    *   Resets VM1's mount of the **Test Bucket**.
    *   Prints instructions.

2.  **On VM2 (Follower):**
    ```bash
    execute_scenario
    # No ID required.
    ```
    *   Detects the active scenario from the shared config.
    *   Resets VM2's mount of the **Test Bucket**.
    *   Prints instructions.

3.  **Completion:**
    *   Run `complete_scenario` (on either VM) to clean up.

---

## Scenario Management & Aliases

These high-level aliases manage the lifecycle of a test scenario.

*   `execute_scenario [ID]`: Starts a scenario (in step mode) or joins an existing one. Resets mounts and prepares the environment.
*   `complete_scenario` / `mark_scenario_completed`: Marks the current scenario as successfully finished. Cleans up temporary state files and finalizes the log. **Must be run at the end of every scenario.**
*   `abort_scenario` / `abort_current_scenario`: Forcibly stops the current scenario without marking it as success. Useful if a test hangs or you want to restart.
*   `fail_scenario`: Explicitly marks the scenario as FAILED in the log and cleans up.

---

## File System Operations Reference

These aliases run the actual file system tests. They often have assertions built-in (e.g., `readfileandfail` expects the read to fail).

**Basic File Operations**
*   `createfile`: Creates `sample.txt` with default content.
*   `createfilewith2ndcontent`: Creates `sample.txt` with "sample_content2".
*   `create2ndfile`: Creates `sample2.txt`.
*   `readfile`: Reads `sample.txt` and prints content.
*   `readfilehasoriginalcontent`: Reads `sample.txt` and asserts it contains "sample_content".
*   `readfilehasupdatedcontent`: Reads `sample.txt` and asserts it contains "sample_content2".
*   `updatefile`: Overwrites `sample.txt` with new content.
*   `deletefile`: Deletes `sample.txt`.
*   `listfile`: Checks if `sample.txt` exists (via `ls`).
*   `renamefile`: Renames `sample.txt` to `sample2.txt`.

**Negative Testing (Expect Failure)**
*   `readfileandfail`: Tries to read `sample.txt`, succeeds if the read FAILS.
*   `listfileandfail`: Tries to list `sample.txt`, succeeds if it does NOT exist.
*   `read2ndfileandfail`: Tries to read `sample2.txt`, succeeds if it FAILS.

**Directory Operations**
*   `createdir`: Creates `sample_dir`.
*   `listdir`: Checks if `sample_dir` exists.
*   `deletedir`: Deletes `sample_dir`.
*   `renamedir`: Renames `sample_dir` to `sample_dir2`.
*   `listdirandfail`: Checks if `sample_dir` does NOT exist.

**Symlink Operations**
*   `createsymlink`: Creates `sample.lnk` pointing to `sample.txt`.
*   `listsymlink`: Checks if the symlink exists.
*   `readfromsymlink`: Reads the target content via the symlink.
*   `deletesymlink`: Deletes the symlink.
*   `listsymlinkandfail`: Checks if symlink is gone.

**Advanced I/O (Go-based)**
*   `writedirectfile`: Writes using `O_DIRECT`.
*   `readdirectfile`: Reads using `O_DIRECT`.
*   `writefilewithoutsync`: Writes without calling `fsync()`.
*   `writebigfile`: Writes a large (2GB) file.
*   `writebigfileconcurrently`: Spawns multiple threads to write to the same large file simultaneously (stress test).

---

## Asynchronous & Interactive Operations

### Asynchronous Operations
Some operations, particularly those involving large file writes in stress tests, run in the background.

*   **`writebigfileasync`**: Starts writing a large file (2GB) in the background.
*   **`writedirectbigfileasync`**: Starts writing a large file with `O_DIRECT` in the background.
*   **`waitforbackgroundjobs`**: Blocks until all currently running background jobs (started by the above commands) have finished.

**Usage Pattern:**
```bash
# Start simultaneous writes (e.g., in a dual-node scenario)
mount1
writebigfileasync &  # Start job 1
mount2
writebigfileasync &  # Start job 2
waitforbackgroundjobs # Wait for both to finish
```

### Interactive / Blocking Operations
These operations intentionally block execution to simulate specific file handle states (e.g., holding a file open without flushing). **You must manually interrupt them.**

*   **`writefilewithoutflush`**: Writes data but does NOT close the file descriptor. It hangs indefinitely to keep the handle open.
*   **`writedirectfilewithoutflush`**: Same as above, but with `O_DIRECT`.
*   **`writefilewithoutsyncorflush`**: Same as above, but also skips `fsync()`.

**Usage Pattern:**
1.  Run the command: `writefilewithoutflush`
2.  The terminal will show: `>> Waiting for interrupt signal (Ctrl+C) to exit...`
3.  Perform your check (e.g., verify file visibility from another mount).
4.  Press **Ctrl+C** to interrupt the process, forcing it to close the handle and (usually) flush data.

---

## Configuration

**Setting Config:**
Modify `workflow_config` in the root directory or use python to update specific settings (like sleep time).

**Querying Config:**
*   `current_config`: Displays global settings.
*   `current_scenario`: Shows the active scenario name.
*   `current_logfile`: Shows the path to the active log file.

---

## Logging & Debugging

**Log Persistence:**
*   **Dual Node:** Logs are safe in the `SHARED_BUCKET`.
*   **Single Node:** Logs are in `~/work/tasks` (Local SSD). **You must manually copy these to the shared bucket** if you wish to preserve them after deleting the VM.
    ```bash
    cp -r ~/work/tasks/coherency-validation/python/single_node_single_mount/exec_log_*.log ~/work/shared/saved_logs/
    ```

**Manual Logging:**
```bash
log_custom "Observation: File appeared after 3 seconds."
```

---

## Troubleshooting

*   **"No scenario currently running"**:
    *   For `dual_node`, ensure VM1 started the scenario first.
    *   Check if `$HOME/work/shared` is mounted correctly on both VMs.
*   **Git Errors / "Unable to read tree"**:
    *   Avoid running git commands inside the `~/work/shared` mount if it's a simple copy. Perform git operations in `/tmp` and copy files over.
*   **Indentation/Syntax Errors**:
    *   Check `execute_scenarios.py`.
