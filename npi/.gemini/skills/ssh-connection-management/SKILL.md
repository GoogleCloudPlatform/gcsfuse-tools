---
name: ssh-connection-management
description: Guides on managing persistent SSH sockets and multiplexing connections to target VMs.
---

# SSH Connection Management for GCSFuse NPI

This skill guides you through establishing and managing persistent SSH socket multiplexing connections to target GCE VMs or intermediate VMs. Multiplexing speeds up command execution and keeps runs resilient to transient network dropouts.

## Setup Socket Directory

Before starting, ensure the socket cache directory exists on the local machine:
```bash
mkdir -p ~/.ssh/sockets
```

## Establish Master Connection

For each target VM or intermediate VM configured (e.g., in `targets.json`):

1.  **Identify Connection Details**:
    Get the target's VM Name, Zone, GCP Project ID, and SSH User.

2.  **Clean Up Stale Socket File**:
    If a connection was aborted or timed out, the socket file might still exist but be dead. Delete it first:
    ```bash
    rm -f ~/.ssh/sockets/<TARGET_NAME>.sock
    ```
    *(Note: `<TARGET_NAME>` is typically the target's name from `targets.json`, e.g., `gce-c4-ssd` or `gke-intermediate-vm`)*

3.  **Start Persistent Master Connection**:
    Run the SSH command in the background or in a persistent terminal session to establish the master socket:
    ```bash
    ssh -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com
    ```
    Parameters to replace:
    - `<TARGET_NAME>`: Unique name/identifier for the target connection socket.
    - `<SSH_USER>`: The SSH username (typically the local user executing the task).
    - `<VM_NAME>`: The name of the GCE VM or the GKE intermediate VM.
    - `<ZONE>`: The GCP zone where the VM resides.
    - `<PROJECT_ID>`: The GCP project ID.

4.  **Verify Socket Status**:
    Confirm the socket has been successfully created:
    ```bash
    ls -la ~/.ssh/sockets/
    ```
    You should see `<TARGET_NAME>.sock` listed.

## Verify Connection Alive

To verify if the multiplexed connection is active and responding, test it with a simple echo command:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "echo 'Connection Alive'"
```

## Refreshing/Recreating Sockets

If user permissions change on the target VM (e.g., after adding the user to the `docker` group):
1.  Close the active socket by removing the socket file:
    ```bash
    rm -f ~/.ssh/sockets/<TARGET_NAME>.sock
    ```
2.  Re-run the SSH master connection command to re-initialize.
