---
name: benchmark-build-setup
description: Guides on checking out GCSFuse, configuring GCE target VMs and GKE runner VMs with RAID0 or tmpfs RAM disk storage buffers via raid0-script.sh, setting up Docker and Artifact Registry permissions, building and pushing benchmarking container images for both GCE and GKE runs using build_images.py, and managing socket recreation after user group modifications.
---

# Benchmark Build and Setup for GCSFuse NPI (GCE & GKE)

This skill guides you through checking out the GCSFuse repository, configuring target GCE VMs and GKE intermediate runner VMs with storage buffers (RAID0 or `tmpfs` RAM disk) and Docker, and building/pushing benchmark images to Google Artifact Registry for use across **both GCE VM and GKE cluster benchmark runs**.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **Active Master SSH Connection**: Established multiplexed SSH connection socket at `~/.ssh/sockets/<TARGET_NAME>.sock` (for GCE VMs and GKE intermediate runner VMs).
2. **GCSFuse Repository Access**: Local clone or submodule checkout of the GCSFuse repository (default branch standard is `master`).
3. **Artifact Registry Access**: Permissions to push container images to Google Artifact Registry (`us-docker.pkg.dev`) authenticated via `gcloud auth configure-docker us-docker.pkg.dev`.
4. **Python 3 Environment**: Local Python 3 environment with dependencies for `build_images.py`.
5. **Isolated KUBECONFIG Environment**: Strict environment policy requiring `mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig` prior to any cluster credential fetching (`gcloud container clusters get-credentials`) or `kubectl` operations to prevent mutating the default host `~/.kube/config`.
6. **GCS Storage Buckets Provisioned**: Target Regional HNS or Zonal RAPID HNS buckets created via **[Bucket Creation](../bucket-creation/SKILL.md)**.

### Trigger Conditions
- Executed prior to running the benchmark suite (`npi_orchestrator.py`) to prepare target environments and container images for **both GCE VM and GKE cluster** benchmarks.
- Required when container benchmark images need to be rebuilt or updated with new GCSFuse binary versions.
- Required when initializing or checking local SSD / RAM disk storage buffers on target GCE VMs or GKE runner VMs.

## Input/Output Contract

### Inputs
- **`targets.json`**: JSON array containing target specifications (`vm_name`, `zone`, `buffer_mount`, `has_ssd`, `type`).
- **`raid0-script.sh`**: Shell script executed on target VM to assemble RAID0 array or mount `tmpfs` RAM disk.
- **`build_images.py`**: Python script used to build and push benchmarking Docker images.
- **Custom Matrices** (Optional): `fio/read_matrix.csv` and `fio/write_matrix.csv` for smoke-testing matrix overrides.
- **CLI Parameters**: `--project`, `--image-version`, `--gcsfuse-version` (defaults to `master`).

### Outputs
- **Mounted Storage Buffer**: `/mnt/lssd` (RAID0 array on local SSDs) or `/tmp/npi_buffer` (500GB `tmpfs` RAM disk) on target VM.
- **Docker Group Membership**: Target SSH user added to `docker` group on VM.
- **Artifact Registry Image**: Pushed Docker image tagged as `us-docker.pkg.dev/<PROJECT_ID>/gcsfuse-npi-images:<IMAGE_VERSION>`.
- **Recreated SSH Socket**: Re-established master connection socket with updated group memberships.

## Step-by-Step Procedure

### Step 1: Provision Buckets & Prepare GCSFuse Workspace

1. **Provision Target Storage Buckets**:
   Verify or create Regional HNS or Zonal RAPID HNS buckets by following the **[Bucket Creation](../bucket-creation/SKILL.md)** skill.
2. **Clone / Verify GCSFuse Repository**:
   Verify that the GCSFuse repository is checked out locally to the target branch or tag (default standard is `master`).
3. **Smoke-Test Matrix Customization** (Optional):
   If performing quick validation or smoke tests, edit matrix files to run minimal iterations:
   - Edit: `fio/read_matrix.csv`
   - Edit: `fio/write_matrix.csv`
   *(Note: Remember to run `git restore fio/read_matrix.csv fio/write_matrix.csv` after image building).*

### Step 2: Configure Target VMs

Configure the storage buffer and Docker workspace on each target VM using the established SSH master connection socket.

#### A. Configure Storage Buffer
> [!NOTE]
> For TPU GCE VMs (and targets without local SSDs, `has_ssd: false`), the buffer is allocated in memory using `tmpfs`. RAID0 assembly is skipped.

1. **Check for Existing Mount First**:
   Before running setup, check if `buffer_mount` is already mounted (resolving `SSH_USER="${SSH_USER:-$(gcloud config get-value account 2>/dev/null | tr '@.' '_')}"`):
   ```bash
   SSH_USER="${SSH_USER:-$(gcloud config get-value account 2>/dev/null | tr '@.' '_')}"
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "mountpoint -q <SSD_MOUNT_PATH> && echo 'Already mounted' || echo 'Not mounted'"
   ```
   Check if a RAID0 array (`/dev/md0`) is already active:
   ```bash
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "df -h | grep -E '^/dev/md'"
   ```

2. **Unified Buffer Setup (Run only if not already mounted)**:
   If not mounted, copy and execute `raid0-script.sh` on the target VM. The script builds a RAID0 array from local SSDs if present. If no local SSDs exist, it dynamically allocates a `tmpfs` RAM disk (sizing up to 50% of physical host RAM when RAM < 550GB, e.g., 64GB on a 128GB VM, or 500GB when RAM >= 550GB):
   ```bash
   # Copy script to target
   scp -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine raid0-script.sh ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com:~/raid0-script.sh

   # Execute script with mount path parameter
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash ~/raid0-script.sh <SSD_MOUNT_PATH>"
   ```

#### B. System Package Self-Healing & Docker Setup
Execute self-healing package pre-checks for missing system build packages (`build-essential`, `make`, `docker.io`), install Docker, and add the SSH user to the `docker` group:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "if ! command -v make &>/dev/null || ! command -v gcc &>/dev/null || ! command -v docker &>/dev/null; then echo 'Installing system packages...'; sudo apt-get update && sudo apt-get install -y build-essential make docker.io; fi && sudo usermod -aG docker \$USER"
```

> [!CRITICAL]
> **Socket Recreation Required**: Group memberships are evaluated only at SSH session startup. After running `usermod -aG docker`, you MUST close and recreate the SSH multiplexing socket to apply docker group permissions:
> 1. Close socket after checking liveness: `ssh -O check -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null && (ssh -O exit -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null ; rm -f ~/.ssh/sockets/<TARGET_NAME>.sock)`
> 2. Re-establish master connection using `ssh-connection-management`.

#### C. Configure Registry Access on Target
Enable target VM Docker daemon to pull images from Artifact Registry:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "gcloud auth configure-docker us-docker.pkg.dev -q"
```

### Step 3: Build & Push Benchmark Images

Build and push benchmarking container images (with FIO and Go-Client) to Google Artifact Registry:

1. **Configure Registry Auth Locally**:
   ```bash
   gcloud auth configure-docker us-docker.pkg.dev
   ```
2. **Execute Build Script**:
   ```bash
   python3 build_images.py --project <PROJECT_ID> --image-version <IMAGE_VERSION> --gcsfuse-version master
   ```
3. **Restore Matrices**:
   If customized in Step 1, revert matrix files to clean working state:
   ```bash
   git restore fio/read_matrix.csv fio/write_matrix.csv
   ```

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **Docker Permission Denied on Target** | User added to `docker` group, but SSH session retains stale group ID token | Close active socket (`rm -f ~/.ssh/sockets/<TARGET_NAME>.sock`) and recreate master SSH connection (`ssh -N -M -S ...`). |
| **Missing System Packages** | VM image lacks build tools or Docker runtime | Self-healing check auto-executes `sudo apt-get update && sudo apt-get install -y build-essential make docker.io`. |
| **RAID0 Setup Fails (`has_ssd: true`)** | Local SSD NVMe devices not detected or busy | Check `lsblk`. If no SSDs exist, update `targets.json` to set `has_ssd: false` and re-run `raid0-script.sh` to use RAM disk. |
| **Insufficient RAM for 500GB RAM Disk (`has_ssd: false`)** | Machine RAM < 550GB | Dynamically allocate `tmpfs` RAM disk up to 50% of host physical RAM (and cap test dataset sizes accordingly), or attach Local SSDs to host VM. |
| **Matrix Customizations Left Dirty** | Smoke-test matrix edits accidentally committed or left un-restored | Execute `git restore fio/read_matrix.csv fio/write_matrix.csv` immediately after `build_images.py` finishes. |
| **Artifact Registry Authentication Denied** | Missing `roles/artifactregistry.writer` role or expired `gcloud` auth token | Run `gcloud auth login` and `gcloud auth configure-docker us-docker.pkg.dev`. Ensure GCP account has Artifact Registry permissions. |

## Verification Checks

1. **Verify Artifact Registry Image**:
   Confirm the build image exists and tag matches `<IMAGE_VERSION>`:
   ```bash
   gcloud artifacts docker images list us-docker.pkg.dev/<PROJECT_ID>/gcsfuse-npi-images --image-format='value(format("{0}:{1}",package,tag))' | grep "<IMAGE_VERSION>"
   ```

2. **Verify Remote Buffer Mount**:
   Verify that the storage path is mounted on target VM:
   ```bash
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "mountpoint -q <SSD_MOUNT_PATH> && echo 'MOUNTED_OK'"
   ```

3. **Verify Remote Docker Access**:
   Verify SSH user can execute `docker` without `sudo`:
   ```bash
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "docker ps"
   ```
