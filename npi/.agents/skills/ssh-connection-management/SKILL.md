---
name: ssh-connection-management
description: Guides on establishing persistent master SSH multiplexing sockets at ~/.ssh/sockets/<TARGET_NAME>.sock using ssh -f -N -M -S, testing connection liveness, removing stale control sockets, and recreating sockets following user group modifications like docker usermod updates.
---

# SSH Connection Management for GCSFuse NPI

This skill guides you through establishing, managing, and troubleshooting persistent SSH socket multiplexing connections to target GCE VMs or GKE intermediate controller VMs. Socket multiplexing speeds up command execution and maintains session resilience across automated workflow steps.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **GCP Compute SSH Keys**: Local SSH key pair present at `~/.ssh/google_compute_engine` (or standard `~/.ssh/id_rsa`).
2. **GCP Authentication**: Local `gcloud` authenticated with compute viewer/admin permissions.
3. **Network Reachability**: Internal IP or hostname reachability to target VM (e.g. `nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com`).
4. **OpenSSH Client**: Local OpenSSH client supporting ControlMaster options (`-M`, `-S`).

### Trigger Conditions
- Executed prior to running remote commands, setup scripts, conformance test suites, or benchmark runs on target GCE VMs or GKE intermediate VMs.
- Triggered when SSH connections fail due to stale socket files ("Control socket connect failed").
- Triggered when user permissions on target VM are modified (e.g., adding user to `docker` group) requiring session group ID refresh.

## Input/Output Contract

### Inputs
- **Target Connection Details**:
  - Target Name (`<TARGET_NAME>`, e.g., `gce-c4-ssd` from `targets.json`).
  - VM Name (`<VM_NAME>`).
  - Zone (`<ZONE>`).
  - GCP Project ID (`<PROJECT_ID>`).
  - SSH User (`<SSH_USER>`): Username used for SSH authentication. Resolved dynamically via `SSH_USER="${SSH_USER:-$(gcloud config get-value account 2>/dev/null | tr '@.' '_')}"` or falling back to local OS username logic (`local_user_google_com`).
- **Socket Cache Directory**: `~/.ssh/sockets/`.

### Outputs
- **Active Master Socket File**: Unix domain socket located at `~/.ssh/sockets/<TARGET_NAME>.sock`.
- **Background SSH Process**: Persistent background `ssh -f -N -M` process holding the master channel open.

## Step-by-Step Procedure

### Step 1: Create Socket Cache Directory

Ensure local socket directory exists:
```bash
mkdir -p ~/.ssh/sockets
```

### Step 2: Clean Up Stale Sockets

Before starting a master connection, perform a non-destructive liveness check and remove socket files only if the master process is unresponsive:
```bash
if ! ssh -O check -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null; then
  ssh -O exit -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null || pkill -f "ssh -f -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock"
  rm -f ~/.ssh/sockets/<TARGET_NAME>.sock
fi
```

### Step 3: Establish Master SSH Connection

Launch the persistent master SSH connection in the background (or persistent terminal), resolving `SSH_USER` dynamically:
```bash
SSH_USER="${SSH_USER:-$(gcloud config get-value account 2>/dev/null | tr '@.' '_')}"
ssh -f -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine ${SSH_USER}@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com
```

Key options explained:
- `-f`: Requests ssh to go to background just before command execution.
- `-N`: Do not execute a remote command (background connection mode).
- `-M`: Place the SSH client into master mode for connection sharing.
- `-S ~/.ssh/sockets/<TARGET_NAME>.sock`: Path to the control socket.
- `-o IdentitiesOnly=yes -i ~/.ssh/google_compute_engine`: Restrict authentication strictly to the specified compute key, avoiding identity rejection.
- `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`: Prevent interactive host key prompts.

### Step 4: Verify Connection Liveness

Test remote command execution over the master socket:
```bash
ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "echo 'Connection Alive'"
```

### Step 5: Refreshing / Recreating Sockets

If user permissions change on the remote VM (e.g., after adding the user to the `docker` group via `usermod -aG docker`):
1. Gracefully terminate and remove the master socket after checking liveness:
   ```bash
   ssh -O exit -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null || pkill -f "ssh -f -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock"
   rm -f ~/.ssh/sockets/<TARGET_NAME>.sock
   ```
2. Re-establish the master connection by repeating Step 3.

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **`Control socket connect failed: Connection refused`** | Master SSH process died unexpectedly, leaving a dead socket file | Check socket liveness (`ssh -O check -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null`). If check fails, terminate process and delete stale socket file (`ssh -O exit -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null || pkill -f "ssh -f -N -M -S ~/.ssh/sockets/<TARGET_NAME>.sock" ; rm -f ~/.ssh/sockets/<TARGET_NAME>.sock`) and re-run master connection command. |
| **`Permission Denied (publickey)`** | SSH key `~/.ssh/google_compute_engine` missing or expired GCP IAM SSH login credentials | Ensure `-o IdentitiesOnly=yes -i ~/.ssh/google_compute_engine` is passed. Run `gcloud compute config-default-ssh-keys` or `gcloud compute ssh <VM_NAME> --zone=<ZONE>` to refresh SSH keys. |
| **Permission Group Refresh Delay (Docker)** | Added user to `docker` group, but commands fail with `permission denied while trying to connect to Docker daemon` | Active SSH master session retains original group IDs. Terminate processes and remove sockets (`pkill -f "ssh -f -N -M -S ~/.ssh/sockets/" ; rm -f ~/.ssh/sockets/*.sock`) and start new master SSH socket. |
| **Connection Drop / Network Disconnect** | Remote VM rebooted or network path reset | Remove stale socket after checking with `ssh -O check` and re-establish master SSH connection. |

## Verification Checks

1. **Local Socket Liveness Check**: Confirm socket file exists and master process is alive:
   ```bash
   ssh -O check -S ~/.ssh/sockets/<TARGET_NAME>.sock 2>/dev/null && echo "SOCKET_ALIVE"
   ```
2. **Remote Echo Check**: Confirm commands execute over multiplexed socket:
   ```bash
   ssh -S ~/.ssh/sockets/<TARGET_NAME>.sock -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/google_compute_engine <SSH_USER>@nic0.<VM_NAME>.<ZONE>.c.<PROJECT_ID>.internal.gcpnode.com "echo 'Connection Alive'"
   ```
