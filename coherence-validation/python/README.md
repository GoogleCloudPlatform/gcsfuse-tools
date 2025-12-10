# GCS Coherency Validation Tool - User Manual

This tool facilitates comprehensive validation of `gcsfuse` consistency, coherency, and caching behaviors. It supports various testing workflows, including single-node testing with one or two mounts, and a distributed dual-node testing workflow.

## Table of Contents

1.  [Prerequisites & Setup](#prerequisites--setup)
2.  [Directory Structure](#directory-structure)
3.  [Workflows Overview](#workflows-overview)
4.  [Getting Started](#getting-started)
5.  [Workflow 1: single_node_single_mount](#workflow-1-single_node_single_mount)
6.  [Workflow 2: single_node_dual_mounts](#workflow-2-single_node_dual_mounts)
7.  [Workflow 3: dual_node_mounts](#workflow-3-dual_node_mounts)
8.  [Configuration](#configuration)
9.  [Logging & Debugging](#logging--debugging)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites & Setup

### 1. VM Provisioning
*   **Single Node Workflows:** Require 1 VM.
*   **Dual Node Workflow:** Requires **2 VMs** (referred to as VM1/Leader and VM2/Follower).
*   **OS:** Linux (Ubuntu/Debian recommended).
*   **Dependencies:** Python 3, Go (for direct I/O tests), `gcsfuse` installed.

### 2. Configure Hostnames (For Dual Node)
If running the `dual_node_mounts` workflow, update `dual_node_mounts/config.py` to recognize your VM hostnames.
*   Edit `shared/coherency-validation/python/dual_node_mounts/config.py`.
*   Update the logic mapping hostname to `MOUNT_NUMBER` (1 or 2).

### 3. Mount Shared Storage
For `dual_node_mounts`, both VMs must share state files.
*   **Bucket:** `gargnitin-coherency-work-shared-bucket-asiase1` (or your equivalent).
*   **Mount Point:** `$HOME/work/shared`.
*   **Mount Command:**
    ```bash
    mkdir -p $HOME/work/shared
    gcsfuse --implicit-dirs gargnitin-coherency-work-shared-bucket-asiase1 $HOME/work/shared
    ```

### 4. Create Workspace Directories
Create the following directories on **all** VMs:
```bash
mkdir -p $HOME/work/tasks
mkdir -p $HOME/work/test_buckets
```
*   `~/work/tasks`: Stores local logs and state for single-node workflows.
*   `~/work/test_buckets`: Base directory where the tool creates test mount points (e.g., `gargnitin-test-hns-asiase1-mount1`).

---

## Directory Structure

The tool logic resides in `~/work/shared/coherency-validation/python` (mapped from the repo).

*   `workflow_aliases.sh`: **Entry point.** Defines shell aliases for easy interaction.
*   `fsops.py`: Core library for file system operations.
*   `write.go` / `read.go`: Helpers for specialized I/O (O_DIRECT, concurrent writes).
*   `single_node_single_mount/`: Workflow logic for 1 VM, 1 Mount.
*   `single_node_dual_mounts/`: Workflow logic for 1 VM, 2 Mounts.
*   `dual_node_mounts/`: Workflow logic for 2 VMs, 1 Mount each (distributed).

---

## Workflows Overview

| Workflow | Description | Use Case | Log Location |
| :--- | :--- | :--- | :--- |
| **single_node_single_mount** | 1 VM, 1 Mount point. | Basic sanity checks, concurrent reads/writes on same mount. | `~/work/tasks/...` |
| **single_node_dual_mounts** | 1 VM, 2 Mount points. | Local coherency (does Mount 2 see Mount 1's changes?). | `~/work/tasks/...` |
| **dual_node_mounts** | 2 VMs, 1 Mount each. | Distributed coherency (does VM 2 see VM 1's changes?). | `~/work/shared/...` (Preserved in bucket) |

---

## Getting Started

1.  **Navigate to the tool directory:**
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
    *This configures the environment aliases (`execute_scenario`, `createfile`, etc.) for the chosen workflow.*

---

## Workflow 1: single_node_single_mount

**Execution:**
This workflow runs automated tests on a single mount point.

*   **List Scenarios:**
    ```bash
    execute_scenario --list
    ```
*   **Run Scenario (Step-by-Step Mode):**
    ```bash
    execute_scenario <ID>
    # Performs setup, then waits. You run the printed commands manually.
    ```
*   **Run Scenario (Complete/Automated Mode):**
    ```bash
    execute_scenario_complete <ID>
    # Runs the entire scenario automatically.
    ```

**Supported Scenarios:**
*   Basic CRUD: Create/Read/Update/Delete/Rename File & Directory.
*   Symlinks: Create/Read/Delete/Move.
*   Sync/Flush: Write without sync/flush (tests persistence).
*   O_DIRECT: Create/Update/Read with Direct I/O.
*   Concurrency: Read/Write large files concurrently from multiple threads.

---

## Workflow 2: single_node_dual_mounts

**Execution:**
This workflow mounts the **same bucket twice** locally (Mount 1 & Mount 2) to test visibility.

*   **List Scenarios:**
    ```bash
    execute_scenario --list
    ```
*   **Run Scenario (Step-by-Step Mode):**
    ```bash
    execute_scenario <ID>
    # Sets up Mount 1 & 2. Instructions tell you which mount to use (e.g., "mount1 ; createfile ; mount2 ; listfile").
    ```
*   **Run Scenario (Complete/Automated Mode):**
    ```bash
    execute_scenario_complete <ID>
    # Automatically switches directories between Mount 1 and Mount 2 to execute commands.
    ```

**Supported Scenarios:**
*   Standard Cross-Mount ops: Create on M1 -> List on M2.
*   Negative tests: Delete on M1 -> Expect "Not Found" on M2.
*   Concurrency: Simultaneous writes from both mounts.

---

## Workflow 3: dual_node_mounts

**Execution:**
This requires coordination between VM1 (Leader) and VM2 (Follower).

**Configuration:**
*   **Shared Log:** The log file is created in `$HOME/work/shared/...`. Both VMs write to this file.
*   **Sleep Safety:** Every write to a shared file (including the log) is followed by a configurable sleep (default 15s) to ensure metadata propagation in `gcsfuse`.

**Steps:**

1.  **On VM1 (Leader):**
    ```bash
    execute_scenario <ID>
    ```
    *   This initializes the test, resets VM1's mount, and prints instructions for VM1.
    *   *Perform the FS operations manually as instructed.*

2.  **On VM2 (Follower):**
    ```bash
    execute_scenario
    # No ID needed; it detects the active scenario from the shared config file.
    ```
    *   This joins the session, resets VM2's mount, and prints instructions for VM2.
    *   *Perform the FS operations manually.*

3.  **Completion:**
    *   Once finished, run `complete_scenario` (on either VM) to clean up and finalize the log.

**Supported Scenarios:**
*   Cross-Node CRUD (Create on VM1 -> Read on VM2).
*   Concurrent Writes (Scenario 13 & 27): Writing to the same large file from both VMs simultaneously.
    *   *Note:* Logging of FS operations is disabled for these scenarios to minimize latency. Headers are still logged.

---

## Configuration

**Setting Config:**
You can modify the global configuration (e.g., sleep times, cache settings).
```bash
# Example: Change sleep time to 5 seconds
python3 -c "import json; d=json.load(open('workflow_config')); d['sleep_seconds_after_shared_file_write']=5; json.dump(d, open('workflow_config','w'))"
```

**Querying Config:**
*   `current_config`: Displays global settings.
*   `current_scenario`: Shows the active scenario name.
*   `current_logfile`: Shows the path to the active log file.

---

## Logging & Debugging

**Log Location:**
*   **single_node_...**: Logs are in `$HOME/work/tasks/coherency-validation/python/<workflow>/`.
    *   *Note:* These are NOT automatically preserved in the shared bucket. You must manually copy them to `$HOME/work/shared/...` if you want to save them.
*   **dual_node_mounts**: Logs are in `$HOME/work/shared/coherency-validation/python/dual_node_mounts/`.
    *   *Note:* These are automatically preserved in the GCS bucket.

**Log Contents:**
*   Execution Banner ("Executing Scenario...")
*   Mount Operations (Unmount/Mount/Drop Cache)
*   Command Execution (e.g., `[timestamp] [host] $ createfile ...`)
*   Exit Codes and Output.

**Manual Logging:**
You can inject custom messages into the log:
```bash
log_custom "Starting iteration 2..."
```

---

## Troubleshooting

*   **"No scenario currently running"**:
    *   For `dual_node`, ensure VM1 started the scenario first.
    *   Ensure `$HOME/work/shared` is mounted and accessible.
*   **Git Errors / "Unable to read tree"**:
    *   The `shared` directory might be a symlink or nested repo. Do not run git commands inside the runtime directory; use the source repo.
*   **Indentation/Syntax Errors**:
    *   Check `execute_scenarios.py`. Ensure Python indentation is consistent (2 spaces or 4 spaces).
