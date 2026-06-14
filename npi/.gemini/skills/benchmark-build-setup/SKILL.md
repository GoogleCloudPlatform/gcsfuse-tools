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
Choose one of the following based on VM hardware:

*   **Case 1: Local SSDs Present (`has_ssd: true`)**
    Mount a RAID0 SSD array on the target VM:
    ```bash
    ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "bash -s" < raid0-script.sh
    ```

*   **Case 2: No Local SSDs (`has_ssd: false`)**
    Mount a `tmpfs` RAM disk to act as a safe buffer:
    ```bash
    ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "sudo mkdir -p /tmp/npi_buffer && sudo mount -t tmpfs -o size=1G tmpfs /tmp/npi_buffer && sudo chown -R \$USER:\$USER /tmp/npi_buffer"
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
