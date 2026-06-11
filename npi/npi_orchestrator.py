#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
import threading
import sys
import datetime
import getpass
import shlex

HOME_DIR = os.path.expanduser("~")
local_user = os.environ.get("USER") or getpass.getuser()
STATE_FILE = os.path.join(HOME_DIR, ".npi/npi_run_state.json")
COMMAND_LOG = os.path.join(HOME_DIR, ".npi/npi_commands.log")
log_lock = threading.Lock()

# Dynamic SSH socket directory resolution:
# Reuse ~/.ssh/sockets if active socket files exist, otherwise fallback to shorter /tmp/ssh-<user>
default_socket_dir = os.path.join(HOME_DIR, ".ssh/sockets")
if os.path.isdir(default_socket_dir) and any(f.endswith(".sock") for f in os.listdir(default_socket_dir)):
    SOCKET_DIR = default_socket_dir
else:
    SOCKET_DIR = os.path.join("/tmp", f"ssh-{local_user}")

# Ensure parent directories for state files, sockets, and logs exist
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
os.makedirs(os.path.dirname(COMMAND_LOG), exist_ok=True)
os.makedirs(SOCKET_DIR, exist_ok=True)
os.chmod(SOCKET_DIR, 0o700)

# Resolve SSH user and GCP Project dynamically
default_ssh_user = f"{local_user}_google_com" if not local_user.endswith("_google_com") else local_user
SSH_USER = os.environ.get("SSH_USER", default_ssh_user)
PROJECT_ID = os.environ.get("PROJECT_ID", "gcs-fuse-test")

# Dynamically resolve repository paths (npi.py, npi_gke.py)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(SCRIPT_DIR, "npi.py")):
    REPO_DIR = SCRIPT_DIR
else:
    REPO_DIR = os.getcwd()

NPI_PY_PATH = os.path.join(REPO_DIR, "npi.py")
NPI_GKE_PY_PATH = os.path.join(REPO_DIR, "npi_gke.py")

def run_ssh_cmd(socket_path, vm_name, zone, cmd, timeout=60):
    """Executes a command on a VM via its persistent SSH multiplexing socket."""
    ssh_cmd = [
        "ssh",
        "-o", f"ControlPath={socket_path}",
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=10m",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
    ]
    
    identity_key = os.path.expanduser("~/.ssh/google_compute_engine")
    if os.path.exists(identity_key):
        ssh_cmd.extend(["-i", identity_key, "-o", "IdentitiesOnly=yes"])
        
    ssh_cmd.extend([
        f"{SSH_USER}@nic0.{vm_name}.{zone}.c.{PROJECT_ID}.internal.gcpnode.com",
        cmd
    ])
    
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = (
            f"[{timestamp}] Executing on {vm_name}: {cmd}\n"
            f"[{timestamp}] Full SSH command: {' '.join(ssh_cmd)}\n"
        )
        with log_lock:
            with open(COMMAND_LOG, "a") as f:
                f.write(log_entry)
    except Exception as e:
        print(f"Error logging command to file: {e}")

    try:
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = (
                f"[{timestamp}] SSH command timed out after {timeout}s on {vm_name}\n"
                f"{'-' * 80}\n"
            )
            with log_lock:
                with open(COMMAND_LOG, "a") as f:
                    f.write(log_entry)
        except Exception as e:
            print(f"Error logging timeout to file: {e}")
        return -1, "", f"SSH command timed out after {timeout} seconds"

    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] Exit code: {res.returncode}\n"
        if res.stderr:
            log_entry += f"[{timestamp}] Stderr: {res.stderr.strip()}\n"
        log_entry += f"{'-' * 80}\n"
        with log_lock:
            with open(COMMAND_LOG, "a") as f:
                f.write(log_entry)
    except Exception as e:
        print(f"Error logging result to file: {e}")

    return res.returncode, res.stdout, res.stderr

def load_state(targets):
    default_state = {}
    for t in targets:
        default_state[t["name"]] = {"status": "PENDING", "pid": None, "last_line": ""}
        
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    for k in default_state:
                        if k not in loaded:
                            loaded[k] = default_state[k]
                    return loaded
        except Exception as e:
            print(f"Error loading state file: {e}")
    return default_state

def save_state(state):
    try:
        tmp_file = STATE_FILE + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except Exception as e:
        print(f"Error saving state file: {e}")

def detect_remote_raid0_mount(socket_path, vm_name, zone):
    """Checks the remote VM for any mounted RAID0 (/dev/md*) devices and returns the mount path."""
    code, out, _ = run_ssh_cmd(
        socket_path, vm_name, zone,
        "df -P | grep -E '^/dev/md[0-9]+' | awk '{print $6}' | head -n 1",
        timeout=15
    )
    if code == 0 and out.strip():
        return out.strip()
    return None

def prep_vm(target, socket_path):
    vm_name = target["vm_name"]
    zone = target["zone"]
    target_name = target["name"]
    
    print(f"[{target_name}] Preparing VM {vm_name}...")
    
    # Ensure destination directory exists on the remote VM
    code, _, err = run_ssh_cmd(socket_path, vm_name, zone, "mkdir -p ~/gcsfuse-tools/npi")
    if code != 0:
        raise RuntimeError(f"Failed to create directory on VM {vm_name}: {err}")
    
    if target["type"] == "gce":
        # Validate RAID0 ssd mount if specified
        buffer_mount = target.get("buffer_mount")
        
        # Try to auto-detect if the RAID0 array is mounted at a different location
        detected_mount = detect_remote_raid0_mount(socket_path, vm_name, zone)
        if detected_mount:
            if buffer_mount != detected_mount:
                print(f"[{target_name}] RAID0 SSD mount auto-detected at '{detected_mount}' (overriding configured '{buffer_mount}')")
                target["buffer_mount"] = detected_mount
                buffer_mount = detected_mount

        if buffer_mount:
            quoted_mount = shlex.quote(buffer_mount)
            code, out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"mountpoint -q {quoted_mount}")
            if code != 0:
                raise RuntimeError(f"Buffer mount path {buffer_mount} is not mounted on VM {vm_name}. Please configure it first.")
        
        # Sync latest npi.py script
        sync_file_to_remote(socket_path, vm_name, zone, NPI_PY_PATH, "~/gcsfuse-tools/npi/npi.py")
        
    elif target["type"] == "gke":
        # Sync latest npi_gke.py script and job spec template
        sync_file_to_remote(socket_path, vm_name, zone, NPI_GKE_PY_PATH, "~/gcsfuse-tools/npi/npi_gke.py")
        sync_file_to_remote(socket_path, vm_name, zone, os.path.join(REPO_DIR, "npi_job_spec.yaml"), "~/gcsfuse-tools/npi/npi_job_spec.yaml")
        
        # Validate node requirements remote GKE VM
        validate_gke_nodes(socket_path, vm_name, zone, target)
        
    print(f"[{target_name}] VM prepared successfully.")

def validate_gke_nodes(socket_path, vm_name, zone, target):
    print(f"[{target['name']}] Validating GKE cluster node requirements on remote VM...")
    cluster_name = target.get("cluster_name", "gke-orbax-benchmark-cluster")
    location = target.get("location", target.get("zone", "europe-west4-a"))
    
    # Fetch credentials on the remote VM first
    cred_cmd = f"gcloud container clusters get-credentials {shlex.quote(cluster_name)} --location {shlex.quote(location)} --project {shlex.quote(PROJECT_ID)}"
    code, _, err = run_ssh_cmd(socket_path, vm_name, zone, cred_cmd, timeout=60)
    if code != 0:
        raise RuntimeError(f"Failed to fetch GKE credentials on remote VM: {err}")
        
    code_cpu, out_cpu, err_cpu = run_ssh_cmd(
        socket_path, vm_name, zone,
        "kubectl get nodes -l '!cloud.google.com/gke-tpu-accelerator' -o jsonpath='{.items[*].metadata.name}'",
        timeout=30
    )
    code_tpu, out_tpu, err_tpu = run_ssh_cmd(
        socket_path, vm_name, zone,
        "kubectl get nodes -l 'cloud.google.com/gke-tpu-accelerator' -o jsonpath='{.items[*].metadata.name}'",
        timeout=30
    )
    
    if code_cpu != 0:
        raise RuntimeError(f"GKE Validation Error: Failed to list GKE CPU nodes on remote VM: {err_cpu.strip()}")
    if code_tpu != 0:
        raise RuntimeError(f"GKE Validation Error: Failed to list GKE TPU nodes on remote VM: {err_tpu.strip()}")

    cpu_count = len(out_cpu.strip().split()) if out_cpu.strip() else 0
    tpu_count = len(out_tpu.strip().split()) if out_tpu.strip() else 0

    print(f"[{target['name']}] GKE Cluster Nodes: {cpu_count} CPU nodes, {tpu_count} TPU nodes.")

    if cpu_count == 0:
        raise RuntimeError("GKE Cluster Error: TPU GKE cluster requires at least one CPU compute node to host system services and CSI drivers.")
    is_tpu = target.get("is_tpu", "google.com/tpu" in target.get("resources_limits", ""))
    if is_tpu and tpu_count == 0:
        raise RuntimeError("GKE Cluster Error: TPU GKE cluster requires at least one TPU node to execute benchmarks.")

def get_last_log_line(socket_path, vm_name, zone, log_path):
    code, out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"tail -n 1 {log_path} 2>/dev/null", timeout=10)
    if code == 0:
        return out.strip()
    return ""

def get_disk_utilization(socket_path, vm_name, zone, path):
    quoted_path = shlex.quote(path)
    code, out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"df -P {quoted_path}", timeout=10)
    if code == 0:
        lines = out.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                use_pct = parts[4].rstrip('%')
                if use_pct.isdigit():
                    return int(use_pct)
    return 0

def monitor_run(target, socket_path, state_lock, state):
    target_name = target["name"]
    vm_name = target["vm_name"]
    zone = target["zone"]
    pid_file = f"/tmp/npi_{target_name}.pid"
    log_file = f"/tmp/output_{target_name}.txt"
    
    print(f"[{target_name}] Monitoring benchmark run on {vm_name}...")
    
    # Get PID from remote file
    pid = None
    for _ in range(5):
        code, out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"cat {pid_file} 2>/dev/null")
        if code == 0 and out.strip().isdigit():
            pid = int(out.strip())
            break
        time.sleep(1)
        
    if pid is None:
        print(f"[{target_name}] Error: Could not retrieve process PID from {pid_file}")
        _, log_out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"tail -n 20 {log_file} 2>/dev/null", timeout=10)
        if log_out.strip():
            print(f"[{target_name}] Startup logs:\n{log_out}")
        with state_lock:
            state[target_name]["status"] = "FAILED"
            save_state(state)
        return

    with state_lock:
        state[target_name]["pid"] = pid
        state[target_name]["status"] = "RUNNING"
        save_state(state)

    last_log_change_time = time.time()
    previous_log_line = ""
    MAX_INACTIVITY_SECS = 600
    
    consecutive_ssh_failures = 0
    MAX_SSH_RETRIES = 3

    while True:
        # Check process status
        running_code, _, _ = run_ssh_cmd(socket_path, vm_name, zone, f"ps -p {pid}", timeout=10)
        
        if running_code != 0 and running_code != 1:
            consecutive_ssh_failures += 1
            print(f"[{target_name}] Warning: Transient SSH connection failure (retry {consecutive_ssh_failures}/{MAX_SSH_RETRIES})...")
            if consecutive_ssh_failures >= MAX_SSH_RETRIES:
                print(f"[{target_name}] Error: SSH connection lost. Aborting monitor.")
                with state_lock:
                    state[target_name]["status"] = "FAILED"
                    state[target_name]["last_line"] = "[ABORTED] SSH connection lost after max retries."
                    save_state(state)
                break
            time.sleep(10)
            continue
            
        consecutive_ssh_failures = 0
        running = (running_code == 0)
        
        last_line = get_last_log_line(socket_path, vm_name, zone, log_file)
        
        with state_lock:
            state[target_name]["last_line"] = last_line

        # Monitor disk space if a buffer mount path is specified
        buffer_mount = target.get("buffer_mount")
        if buffer_mount:
            disk_used = get_disk_utilization(socket_path, vm_name, zone, buffer_mount)
            if disk_used > 85:
                print(f"[{target_name}] WARNING: Disk space utilization of {buffer_mount} exceeded 85% ({disk_used}%). Aborting run...")
                cleanup_remote_run(target, socket_path)
                with state_lock:
                    state[target_name]["status"] = "FAILED"
                    state[target_name]["last_line"] = f"[ABORTED] Disk usage high: {disk_used}%"
                    save_state(state)
                break

        # Check for log progress/activity
        if last_line != previous_log_line:
            last_log_change_time = time.time()
            previous_log_line = last_line
        elif running and (time.time() - last_log_change_time > MAX_INACTIVITY_SECS):
            print(f"[{target_name}] WARNING: Log inactivity timeout of {MAX_INACTIVITY_SECS} seconds exceeded. Aborting run...")
            cleanup_remote_run(target, socket_path)
            with state_lock:
                state[target_name]["status"] = "FAILED"
                state[target_name]["last_line"] = f"[ABORTED] Inactivity timeout of {MAX_INACTIVITY_SECS}s"
                save_state(state)
            break
        
        if not running:
            exit_code_file = f"/tmp/npi_{target_name}.exit"
            exit_code_status, exit_code_out = -1, ""
            
            # Retry loop to read the exit status file safely
            for _ in range(3):
                exit_code_status, exit_code_out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"cat {exit_code_file} 2>/dev/null", timeout=10)
                if exit_code_status == 0 and exit_code_out.strip():
                    break
                time.sleep(1)
            
            log_out = ""
            if not (exit_code_status == 0 and exit_code_out.strip() == "0"):
                _, log_out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"tail -n 20 {log_file} 2>/dev/null", timeout=10)
            
            with state_lock:
                if exit_code_status == 0 and exit_code_out.strip() == "0":
                    state[target_name]["status"] = "SUCCESS"
                    print(f"[{target_name}] Run completed successfully.")
                else:
                    state[target_name]["status"] = "FAILED"
                    print(f"[{target_name}] Run FAILED with exit code {exit_code_out.strip()}! Last logs:\n{log_out}")
                save_state(state)
            break
            
        with state_lock:
            save_state(state)
        time.sleep(10)

def sync_file_to_remote(socket_path, vm_name, zone, local_path, remote_path):
    """Copies a local file to a remote VM path reusing the SSH multiplexing socket."""
    scp_cmd = [
        "scp",
        "-o", f"ControlPath={socket_path}",
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=10m",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        local_path,
        f"{SSH_USER}@nic0.{vm_name}.{zone}.c.{PROJECT_ID}.internal.gcpnode.com:{remote_path}"
    ]
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = (
            f"[{timestamp}] SYNC LOCAL TO REMOTE: {' '.join(scp_cmd)}\n"
            f"{'-' * 80}\n"
        )
        with log_lock:
            with open(COMMAND_LOG, "a") as f:
                f.write(log_entry)
    except Exception as e:
        print(f"Error logging scp to file: {e}")

    try:
        res = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to sync {local_path} to {vm_name}: {res.stderr}")
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Syncing {local_path} to {vm_name} timed out after 60 seconds") from e

def cleanup_remote_run(target, socket_path):
    target_name = target["name"]
    vm_name = target["vm_name"]
    zone = target["zone"]
    
    print(f"[{target_name}] Stopping any active benchmarks on remote environment...")
    
    if target["type"] == "gce":
        # Terminate python orchestrator and stop any benchmark docker containers
        cmd = "pkill -9 -f 'python3.*npi\\.py'; docker ps -a --format '{{.ID}} {{.Image}}' | grep 'gcsfuse-benchmarks' | awk '{print $1}' | xargs -r docker rm -f"
        run_ssh_cmd(socket_path, vm_name, zone, cmd, timeout=30)
    elif target["type"] == "gke":
        # Terminate runner script and delete Kubernetes jobs matching label on GKE VM via SSH
        run_ssh_cmd(socket_path, vm_name, zone, "pkill -9 -f 'python3.*npi_gke\\.py'", timeout=30)
        cleanup_cmd = "kubectl delete jobs -l app=gcsfuse-npi-benchmark --ignore-not-found=true"
        run_ssh_cmd(socket_path, vm_name, zone, cleanup_cmd, timeout=30)

def execute_target(target, args, state_lock, state):
    target_name = target["name"]
    vm_name = target["vm_name"]
    zone = target["zone"]
    socket_path = os.path.join(SOCKET_DIR, f"{target_name}.sock")
    
    if state[target_name]["status"] in ["PENDING", "FAILED"]:
        try:
            cleanup_remote_run(target, socket_path)
            prep_vm(target, socket_path)
            
            # Start run unbuffered
            has_ssd = target.get("has_ssd", target["type"] == "gce") # Default GCE to True, GKE to False if unspecified
            requested_benchmarks = args.benchmarks.split() if isinstance(args.benchmarks, str) else args.benchmarks
            
            # Filter out file-cache tests if no SSD is present
            if not has_ssd:
                active_benchmarks = [b for b in requested_benchmarks if "file_cache" not in b]
                if len(active_benchmarks) < len(requested_benchmarks):
                    skipped = [b for b in requested_benchmarks if "file_cache" in b]
                    print(f"[{target_name}] Skipping file cache benchmarks because target VM has no SSD: {', '.join(skipped)}")
            else:
                active_benchmarks = requested_benchmarks

            is_rapid = target.get("is_rapid_bucket", False)
            if is_rapid:
                grpc_only_benchmarks = [b for b in active_benchmarks if "grpc" in b or b == "host_info"]
                if len(grpc_only_benchmarks) < len(active_benchmarks):
                    skipped_http = [b for b in active_benchmarks if "grpc" not in b and b != "host_info"]
                    print(f"[{target_name}] Skipping HTTP1 benchmarks because RAPID bucket is enabled: {', '.join(skipped_http)}")
                active_benchmarks = grpc_only_benchmarks

            if not active_benchmarks:
                print(f"[{target_name}] Skipping target: no benchmarks to run after filtering.")
                with state_lock:
                    state[target_name]["status"] = "SUCCESS"
                    save_state(state)
                return

            if target["type"] == "gce":
                python_args = [
                    "python3", "-u", f"/home/{SSH_USER}/gcsfuse-tools/npi/npi.py",
                    "--bucket-name", target["bucket"],
                    "--project-id", args.project,
                    "--bq-dataset-id", f"{target['dataset']}_regional",
                    "--image-version", args.image_version,
                    "--iterations", str(args.iterations),
                ]
                if is_rapid:
                    python_args.append("--is-rapid-bucket")
                python_args.extend(["--benchmarks"] + active_benchmarks)
                if target.get("buffer_mount"):
                    python_args.append(f"--buffer-mount-path={target['buffer_mount']}")
                    
                python_cmd = " ".join(shlex.quote(arg) for arg in python_args)
                full_cmd = f"{python_cmd}; echo $? > /tmp/npi_{target_name}.exit"
                bench_cmd = f"nohup sh -c {shlex.quote(full_cmd)} > /tmp/output_{target_name}.txt 2>&1 & echo $! > /tmp/npi_{target_name}.pid"
                
            elif target["type"] == "gke":
                node_sel = target.get("node_selector", "")
                res_lim = target.get("resources_limits", "")
                cluster_name = target.get("cluster_name", "gke-orbax-benchmark-cluster")
                location = target.get("location", target.get("zone", "europe-west4-a"))
                
                python_args = [
                    "python3", "-u", f"/home/{SSH_USER}/gcsfuse-tools/npi/npi_gke.py",
                    "--cluster-name", cluster_name,
                    "--location", location,
                    "--bucket-name", target["bucket"],
                    "--project-id", args.project,
                    "--bq-dataset-id", f"{target['dataset']}_regional",
                    "--image-version", args.image_version,
                    "--node-selector", node_sel,
                    "--resources-limits", res_lim,
                    "--iterations", str(args.iterations),
                ]
                if is_rapid:
                    python_args.append("--is-rapid-bucket")
                
                if not has_ssd:
                    python_args.append("--use-memory-volumes")
                else:
                    if any("file_cache" in b for b in active_benchmarks):
                        python_args.append("--run-file-cache-test")
                
                python_args.extend(["--benchmarks"] + active_benchmarks)
                
                python_cmd = " ".join(shlex.quote(arg) for arg in python_args)
                full_cmd = f"{python_cmd}; echo $? > /tmp/npi_{target_name}.exit"
                bench_cmd = f"nohup sh -c {shlex.quote(full_cmd)} > /tmp/output_{target_name}.txt 2>&1 & echo $! > /tmp/npi_{target_name}.pid"
            
            print(f"[{target_name}] Triggering benchmarks on {vm_name}...")
            code, out, err = run_ssh_cmd(socket_path, vm_name, zone, bench_cmd)
            if code != 0:
                print(f"[{target_name}] Error triggering benchmarks: {err}")
                with state_lock:
                    state[target_name]["status"] = "FAILED"
                    save_state(state)
            else:
                monitor_run(target, socket_path, state_lock, state)
        except Exception as e:
            print(f"[{target_name}] Execution preparation failed: {e}")
            with state_lock:
                state[target_name]["status"] = "FAILED"
                save_state(state)
                
    elif state[target_name]["status"] == "RUNNING":
        print(f"[{target_name}] Resuming monitoring of active run on {vm_name}...")
        try:
            monitor_run(target, socket_path, state_lock, state)
        except Exception as e:
            print(f"[{target_name}] Monitoring failed: {e}")
            with state_lock:
                state[target_name]["status"] = "FAILED"
                save_state(state)
    else:
        print(f"[{target_name}] Run already completed successfully.")

def validate_colocation(target, project_id):
    """Validates that GCS bucket has HNS enabled and is colocated with the VM."""
    bucket_name = target["bucket"]
    vm_zone = target["zone"].lower()
    is_rapid = target.get("is_rapid_bucket", False)
    
    cmd = [
        "gcloud", "storage", "buckets", "describe",
        f"gs://{bucket_name}",
        f"--project={project_id}",
        "--raw",
        "--format=json"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        meta = json.loads(res.stdout) or {}
    except Exception as e:
        raise ValueError(f"Failed to describe GCS bucket '{bucket_name}': {e}")
        
    # Validate HNS
    hns_enabled = (meta.get("hierarchicalNamespace") or {}).get("enabled", False)
    if not hns_enabled:
        raise ValueError(f"Bucket '{bucket_name}' does not have Hierarchical Namespace (HNS) enabled. NPI benchmarks require HNS.")
        
    location = (meta.get("location") or "").lower()
    location_type = (meta.get("locationType") or "").lower()
    
    if is_rapid:
        if location_type != "zone":
            raise ValueError(f"Bucket '{bucket_name}' is configured as a RAPID bucket, but GCS location type is '{location_type}' (expected 'zone').")
            
        data_locs = [loc.lower() for loc in (meta.get("dataLocations") or [])]
        if not data_locs:
            raise ValueError(f"Bucket '{bucket_name}' has no data locations listed in GCS metadata.")
            
        if vm_zone not in data_locs:
            raise ValueError(f"Colocation Error: RAPID bucket '{bucket_name}' is in zone(s) {data_locs}, but VM '{target['vm_name']}' is in zone '{vm_zone}'. They must be in the same zone.")
    else:
        vm_region = "-".join(vm_zone.split("-")[:-1])
        if location_type != "region":
            raise ValueError(f"Bucket '{bucket_name}' is configured as a regional bucket, but GCS location type is '{location_type}' (expected 'region').")
            
        if location != vm_region:
            raise ValueError(f"Colocation Error: Regional bucket '{bucket_name}' is in region '{location}', but VM '{target['vm_name']}' is in region '{vm_region}'. They must be in the same region.")

def main():
    parser = argparse.ArgumentParser(description="GCSFuse NPI Orchestrator")
    parser.add_argument("--config", default="targets.json", help="Path to targets.json configuration file")
    parser.add_argument("--benchmarks", nargs="+", default=["read_grpc", "write_grpc"], help="Space separated benchmarks to run")
    parser.add_argument("--image-version", default="smoke-test", help="Docker image tag")
    parser.add_argument("--project", default="gcs-fuse-test", help="GCP Project")
    parser.add_argument("--iterations", type=int, default=2, help="Number of iterations")
    parser.add_argument("--reset", action="store_true", help="Reset saved state and start a fresh run")
    
    args = parser.parse_args()
    if args.reset and os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
            print("Saved state file cleared for a fresh run.")
        except Exception as e:
            print(f"Warning: Could not clear state file: {e}")
    if isinstance(args.benchmarks, list):
        args.benchmarks = " ".join(args.benchmarks)

    global PROJECT_ID
    PROJECT_ID = os.environ.get("PROJECT_ID", args.project)

    # Load targets configuration file
    config_path = os.path.join(REPO_DIR, args.config) if not os.path.isabs(args.config) else args.config
    if not os.path.exists(config_path):
        print(f"Error: Configuration file not found at {config_path}", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(config_path, "r") as f:
            targets = json.load(f)
        if not isinstance(targets, list):
            raise ValueError("Configuration must be a JSON list of targets.")
        for t in targets:
            if not isinstance(t, dict):
                raise ValueError("Each target in the configuration must be a JSON object.")
            required_keys = ["name", "type", "vm_name", "zone", "bucket", "dataset"]
            if t.get("type") == "gce":
                required_keys.append("buffer_mount")
            missing = [k for k in required_keys if k not in t]
            if missing:
                raise ValueError(f"Target '{t.get('name', 'unknown')}' is missing required fields: {', '.join(missing)}")
            if not all(c.isalnum() or c in '-_' for c in t["name"]):
                raise ValueError(f"Target name '{t['name']}' is invalid. Only alphanumeric characters, dashes, and underscores are allowed.")
            validate_colocation(t, PROJECT_ID)
    except Exception as e:
        print(f"Error parsing configuration file {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    state = load_state(targets)
    print(f"Current State: {json.dumps(state, indent=2)}")

    # Validate that required local files exist before starting
    required_files = [NPI_PY_PATH, NPI_GKE_PY_PATH, os.path.join(REPO_DIR, "npi_job_spec.yaml")]
    for path in required_files:
        if not os.path.exists(path):
            print(f"Error: Required local file not found: {path}", file=sys.stderr)
            sys.exit(1)

    # Startup cleanup is handled concurrently inside each target's execution thread.

    state_lock = threading.Lock()
    threads = []
    
    for t in targets:
        thread = threading.Thread(target=execute_target, args=(t, args, state_lock, state), daemon=True)
        thread.start()
        threads.append(thread)
        
    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Orchestrator] Interrupted by user. Exiting monitor. Background runs will continue on VMs.")
        sys.exit(1)

    # Re-evaluate final state
    state = load_state(targets)
    print("\n--- All Orchestrated Runs Completed ---")
    print(f"Final State: {json.dumps(state, indent=2)}")
    
    all_success = all(state[t["name"]]["status"] == "SUCCESS" for t in targets)
    if all_success:
        print("SUCCESS")
        sys.exit(0)
    else:
        print("FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()
