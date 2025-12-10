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
8.  [Configuration](#configuration)
9.  [Logging & Debugging](#logging--debugging)
10. [Troubleshooting](#troubleshooting)

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
*   **Dual Node Workflow:** Requires **2 VMs** (VM1/Leader and VM2/Follower).
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
*   Change it to your **Test Target Bucket** (e.g., `<user>-test-hns-<region>`).
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
