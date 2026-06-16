---
name: benchmark-build-setup
description: Guides on checking out GCSFuse, configuring targets (Docker/RAM disks), and building/pushing benchmarking images to Artifact Registry.
---

# Benchmark Build and Setup for GCSFuse NPI

This skill guides you through checking out the GCSFuse repository, configuring target VMs with storage buffers and Docker, and building/pushing benchmark images to Google Artifact Registry.

## Step 1: Clone/Prepare GCSFuse & Custom Matrices

1.  **Clone / Verify GCSFuse Repository**:
    Verify that the GCSFuse repository or sub-module is checked out locally to the expected branch or tag.
2.  **Smoke-Test Matrix Customization**:
    If running quick verification or smoke tests, edit the matrix files to execute only minimal iterations:
    *   Edit: `fio/read_matrix.csv`
    *   Edit: `fio/write_matrix.csv`
    *(Note: Remember to run `git restore fio/read_matrix.csv fio/write_matrix.csv` after the images are built and pushed to avoid checking in modified matrices).*

## Step 2: Configure Target VMs

Configure the storage buffer and Docker workspace on each target VM using the established SSH master connection socket.

### A. Configure Storage Buffer
*   **Unified Buffer Setup (Local SSD or RAM Fallback)**:
    Execute `raid0-script.sh` on the target VM, passing the target mount path (from `targets.json`'s `buffer_mount`) as the argument. The script will automatically build a RAID0 array from local SSDs if present. If no local SSDs are found, it will verify that the host has at least 600GB of RAM (minimum 550GB detected due to kernel reservations) and mount a 500GB memory volume (`tmpfs`) at the mount path instead:
    ```bash
    # Copy script to target
    scp -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine raid0-script.sh <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com:~/raid0-script.sh
    
    # Run script with the target mount path argument (e.g. /mnt/lssd)
    ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash ~/raid0-script.sh <SSD_MOUNT_PATH>"
    ```

### B. Install Docker & Configure Permissions
Install Docker on the target VM and add the SSH user to the docker group:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh && sudo usermod -aG docker \$USER && rm get-docker.sh"
```

**CRITICAL**: Since group memberships are only evaluated at session startup, recreate the SSH multiplexing socket to apply the docker group changes:
1.  Close socket: `rm -f ~/.ssh/sockets/<TARGET_NAME>.sock`
2.  Re-establish the connection socket (using the `ssh-connection-management` skill).

### C. Configure Registry Access on Target
Enable the target VM docker daemon to pull images from Artifact Registry:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "gcloud auth configure-docker us-docker.pkg.dev -q"
```

## Step 3: Build & Push Benchmark Images

Build and push GCSFuse benchmarking images (with FIO/Go-Client inside) to your Google Artifact Registry:

1.  **Configure Registry Auth Locally**:
    Ensure local credentials can write to the Artifact Registry:
    ```bash
    gcloud auth configure-docker us-docker.pkg.dev
    ```
2.  **Execute Build Script**:
    ```bash
    python3 build_images.py --project <PROJECT_ID> --image-version <IMAGE_VERSION> --gcsfuse-version <GCSFUSE_VERSION>
    ```
3.  **Restore Matrices**:
    If you customized matrix files in Step 1, revert the local changes:
    ```bash
    git restore fio/read_matrix.csv fio/write_matrix.csv
    ```

## Step 4: Verify Image Availability

Verify that the benchmark image is successfully pushed and available in Artifact Registry:
```bash
gcloud artifacts docker images list us-docker.pkg.dev/<PROJECT_ID>/gcsfuse-npi-images --image-format='value(format("{0}:{1}",package,tag))' | grep "<IMAGE_VERSION>"
```
