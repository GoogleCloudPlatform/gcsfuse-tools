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
SOCKET_DIR = os.path.join(HOME_DIR, ".ssh/sockets")

# Strict KUBECONFIG isolation setup to protect host ~/.kube/config
KUBE_DIR = os.path.join(HOME_DIR, ".kube")
ISOLATED_KUBECONFIG = os.path.join(KUBE_DIR, "npi_kubeconfig")
os.makedirs(KUBE_DIR, exist_ok=True)
os.environ["KUBECONFIG"] = ISOLATED_KUBECONFIG

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
    print(f"[{target['name']}] Validating GKE cluster node requirements...")
    cluster_name = target.get("cluster_name", "gke-orbax-benchmark-cluster")
    location = target.get("location", target.get("zone", "europe-west4-a"))
    
    cred_cmd = f"mkdir -p ~/.kube && export KUBECONFIG=~/.kube/npi_kubeconfig && gcloud container clusters get-credentials {shlex.quote(cluster_name)} --location {shlex.quote(location)} --project {shlex.quote(PROJECT_ID)}"
    code, _, err = run_ssh_cmd(socket_path, vm_name, zone, cred_cmd, timeout=30)
    
    if code != 0:
        print(f"[{target['name']}] Remote VM lacks kubectl or cluster credentials. Attempting remote tool installation...")
        install_cmd = "sudo apt-get update && sudo apt-get install -y kubectl gke-gcloud-auth-plugin"
        run_ssh_cmd(socket_path, vm_name, zone, install_cmd, timeout=120)
        code, _, err = run_ssh_cmd(socket_path, vm_name, zone, cred_cmd, timeout=30)
        if code != 0:
            raise RuntimeError(f"GKE Validation Error: Remote VM {vm_name} failed to get credentials for cluster {cluster_name}: {err.strip()}")

    code_cpu, out_cpu, err_cpu = run_ssh_cmd(
        socket_path, vm_name, zone,
        "export KUBECONFIG=~/.kube/npi_kubeconfig && kubectl get nodes -l '!cloud.google.com/gke-tpu-accelerator' -o jsonpath='{.items[*].metadata.name}'",
        timeout=30
    )
    code_tpu, out_tpu, err_tpu = run_ssh_cmd(
        socket_path, vm_name, zone,
        "export KUBECONFIG=~/.kube/npi_kubeconfig && kubectl get nodes -l 'cloud.google.com/gke-tpu-accelerator' -o jsonpath='{.items[*].metadata.name}'",
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

def get_log_file_stat(socket_path, vm_name, zone, log_path):
    code, out, _ = run_ssh_cmd(socket_path, vm_name, zone, f"stat -c '%Y %s' {log_path} 2>/dev/null", timeout=10)
    if code == 0 and out.strip():
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
    previous_log_stat = ""
    MAX_INACTIVITY_SECS = 14400
    
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
        log_stat = get_log_file_stat(socket_path, vm_name, zone, log_file)
        
        with state_lock:
            state[target_name]["last_line"] = last_line

        # Monitor disk space on buffer mount path or fallback to root volume '/'
        disk_check_path = target.get("buffer_mount") or "/"
        disk_used = get_disk_utilization(socket_path, vm_name, zone, disk_check_path)
        if disk_used > 85:
            print(f"[{target_name}] WARNING: Disk space utilization of {disk_check_path} exceeded 85% ({disk_used}%). Aborting run...")
            cleanup_remote_run(target, socket_path)
            with state_lock:
                state[target_name]["status"] = "FAILED"
                state[target_name]["last_line"] = f"[ABORTED] Disk usage high: {disk_used}%"
                save_state(state)
            break

        # Check for log progress/activity using mtime / size stat change
        if log_stat and log_stat != previous_log_stat:
            last_log_change_time = time.time()
            previous_log_stat = log_stat
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
def monitor_local_run(target_name, state_lock, state, pid_file, log_file):
    print(f"[{target_name}] Monitoring local GKE benchmark run...")
    pid = None
    for _ in range(5):
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        pid = int(content)
                        break
            except Exception:
                pass
        time.sleep(1)

    if pid is None:
        print(f"[{target_name}] Error: Could not retrieve process PID from local {pid_file}")
        with state_lock:
            state[target_name]["status"] = "FAILED"
            save_state(state)
        return

    with state_lock:
        state[target_name]["pid"] = pid
        state[target_name]["status"] = "RUNNING"
        save_state(state)

    while True:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            running = False

        last_line = ""
        if os.path.exists(log_file):
            try:
                with open(log_file, "r") as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
            except Exception:
                pass

        with state_lock:
            state[target_name]["last_line"] = last_line
            save_state(state)

        if not running:
            exit_file = f"{pid_file}.exit"
            exit_code = -1
            if os.path.exists(exit_file):
                try:
                    with open(exit_file, "r") as f:
                        exit_code = int(f.read().strip())
                except Exception:
                    pass

            with state_lock:
                if exit_code == 0:
                    state[target_name]["status"] = "SUCCESS"
                    print(f"[{target_name}] Run completed successfully.")
                else:
                    state[target_name]["status"] = "FAILED"
                    print(f"[{target_name}] Run FAILED with exit code {exit_code}! Last log line: {last_line}")
                save_state(state)
            break

        time.sleep(5)

def execute_target(target, args, state_lock, state):
    target_name = target["name"]
    vm_name = target["vm_name"]
    zone = target["zone"]
    socket_path = os.path.join(SOCKET_DIR, f"{target_name}.sock")
    pid_file = f"/tmp/npi_{target_name}.pid"
    log_file = f"/tmp/output_{target_name}.txt"
    
    with state_lock:
        target_status = state[target_name]["status"]
    
    if target_status in ["PENDING", "FAILED"]:
        try:
            cleanup_remote_run(target, socket_path)
            prep_vm(target, socket_path)
            
            # Start run unbuffered
            has_ssd = target.get("has_ssd", target["type"] == "gce") # Default GCE to True, GKE to False if unspecified
            raw_bench = args.benchmarks.replace(',', ' ') if isinstance(args.benchmarks, str) else ' '.join(args.benchmarks)
            requested_benchmarks = raw_bench.split()
            
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

            raw_dataset = target["dataset"]
            if raw_dataset.endswith("_regional"):
                base_dataset = raw_dataset[:-len("_regional")]
            elif raw_dataset.endswith("_zonal"):
                base_dataset = raw_dataset[:-len("_zonal")]
            else:
                base_dataset = raw_dataset
            dataset_id = f"{base_dataset}_regional"

            if target["type"] == "gce":
                python_args = [
                    "python3", "-u", f"/home/{SSH_USER}/gcsfuse-tools/npi/npi.py",
                    "--bucket-name", target["bucket"],
                    "--project-id", args.project,
                    "--bq-dataset-id", dataset_id,
                    "--image-version", args.image_version,
                    "--iterations", str(args.iterations),
                ]
                if is_rapid:
                    python_args.append("--is-rapid-bucket")
                if args.smoke_mode:
                    python_args.append("--smoke-mode")
                python_args.extend(["--benchmarks"] + active_benchmarks)
                if target.get("buffer_mount"):
                    python_args.append(f"--buffer-mount-path={target['buffer_mount']}")
                    
                python_cmd = " ".join(shlex.quote(arg) for arg in python_args)
                full_cmd = f"{python_cmd}; echo $? > /tmp/npi_{target_name}.exit"
                bench_cmd = f"nohup sh -c {shlex.quote(full_cmd)} > /tmp/output_{target_name}.txt 2>&1 & echo $! > /tmp/npi_{target_name}.pid"
                
            elif target["type"] == "gke":
                node_sel = target.get("node_selector", "")
                res_lim = target.get("resources_limits", "")
                cluster_name = target.get("cluster_name")
                location = target.get("location")
                if not cluster_name or not location:
                    raise ValueError(f"Target '{target_name}' (type=gke) missing required 'cluster_name' or 'location' in targets.json.")
                
                python_args = [
                    "python3", "-u", f"/home/{SSH_USER}/gcsfuse-tools/npi/npi_gke.py",
                    "--cluster-name", cluster_name,
                    "--location", location,
                    "--bucket-name", target["bucket"],
                    "--project-id", args.project,
                    "--bq-dataset-id", dataset_id,
                    "--image-version", args.image_version,
                    "--node-selector", node_sel,
                    "--resources-limits", res_lim,
                    "--iterations", str(args.iterations),
                ]
                if is_rapid:
                    python_args.append("--is-rapid-bucket")
                if args.smoke_mode:
                    python_args.append("--smoke-mode")
                
                if not has_ssd:
                    python_args.append("--use-memory-volumes")
                else:
                    if any("file_cache" in b for b in active_benchmarks):
                        python_args.append("--run-file-cache-test")
                
                python_args.extend(["--benchmarks"] + active_benchmarks)
                
                python_cmd = " ".join(shlex.quote(arg) for arg in python_args)
                full_cmd = f"{python_cmd}; echo $? > /tmp/npi_{target_name}.exit"
                bench_cmd = f"nohup sh -c {shlex.quote(full_cmd)} > /tmp/output_{target_name}.txt 2>&1 & echo $! > /tmp/npi_{target_name}.pid"
            
            if target["type"] == "gke":
                check_gcloud, _, _ = run_ssh_cmd(socket_path, vm_name, zone, "which gcloud", timeout=10)
                if check_gcloud != 0:
                    print(f"[{target_name}] Remote VM lacks gcloud/kubectl. Installing remote tools...")
                    install_cmd = "sudo apt-get update && sudo apt-get install -y kubectl gke-gcloud-auth-plugin google-cloud-cli"
                    run_ssh_cmd(socket_path, vm_name, zone, install_cmd, timeout=120)

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
                
    elif target_status == "RUNNING":
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
    if bucket_name.startswith("gs://"):
        bucket_name = bucket_name[5:]
    
    # For GKE, the benchmarks run on the GKE cluster, so we use its location.
    # For GCE, they run on the GCE VM itself.
    if target.get("type") == "gke":
        run_location = target.get("location") or target["zone"]
    else:
        run_location = target["zone"]
    run_location = run_location.lower()
    
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
        meta = json.loads(res.stdout)
        if not isinstance(meta, dict):
            raise ValueError("Unexpected metadata format (expected a JSON object).")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise ValueError(f"Failed to describe GCS bucket '{bucket_name}': {error_msg}")
    except Exception as e:
        raise ValueError(f"Failed to describe GCS bucket '{bucket_name}': {e}")
        
    # Validate HNS
    hns_meta = meta.get("hierarchicalNamespace")
    hns_enabled = hns_meta.get("enabled", False) if isinstance(hns_meta, dict) else False
    if not hns_enabled:
        raise ValueError(f"Bucket '{bucket_name}' does not have Hierarchical Namespace (HNS) enabled. NPI benchmarks require HNS.")
        
    raw_location = meta.get("location")
    location = raw_location.lower() if isinstance(raw_location, str) else ""
    raw_location_type = meta.get("locationType")
    location_type = raw_location_type.lower() if isinstance(raw_location_type, str) else ""
    
    if is_rapid:
        if location_type != "zone":
            raise ValueError(f"Bucket '{bucket_name}' is configured as a RAPID bucket, but GCS location type is '{location_type}' (expected 'zone').")
            
        custom_placement = meta.get("customPlacementConfig")
        raw_data_locs = meta.get("dataLocations") or (custom_placement.get("dataLocations") if isinstance(custom_placement, dict) else None)
        data_locs = [loc.lower() for loc in raw_data_locs if isinstance(loc, str)] if isinstance(raw_data_locs, list) else []
        if not data_locs:
            raise ValueError(f"Bucket '{bucket_name}' has no data locations listed in GCS metadata.")
            
        # If run_location is a zone (e.g. us-central1-a), ensure it is in data_locs.
        # If run_location is a region (e.g. us-central1), ensure at least one data_loc is in that region.
        loc_parts = run_location.split("-")
        is_zone = loc_parts[-1].isalpha() and len(loc_parts[-1]) == 1
        if is_zone:
            if run_location not in data_locs:
                raise ValueError(f"Colocation Error: RAPID bucket '{bucket_name}' is in zone(s) {data_locs}, but target is in zone '{run_location}'. They must be in the same zone.")
        else:
            region_prefix = run_location + "-"
            if not any(loc.startswith(region_prefix) for loc in data_locs):
                raise ValueError(f"Colocation Error: RAPID bucket '{bucket_name}' is in zone(s) {data_locs}, but target is in region '{run_location}'. The bucket zone must be within the target region.")
    else:
        loc_parts = run_location.split("-")
        is_zone = loc_parts[-1].isalpha() and len(loc_parts[-1]) == 1
        run_region = "-".join(loc_parts[:-1]) if is_zone else run_location
        if location_type != "region":
            raise ValueError(f"Bucket '{bucket_name}' is configured as a regional bucket, but GCS location type is '{location_type}' (expected 'region').")
        
        if location != run_region:
            raise ValueError(f"Colocation Error: Regional bucket '{bucket_name}' is in region '{location}', but target is in region '{run_region}'. They must be in the same region.")

def main():
    parser = argparse.ArgumentParser(description="GCSFuse NPI Orchestrator")
    parser.add_argument("--config", default="targets.json", help="Path to targets.json configuration file")
    parser.add_argument("--benchmarks", nargs="+", default=["read_grpc", "write_grpc"], help="Space separated benchmarks to run")
    parser.add_argument("--image-version", default="smoke-test", help="Docker image tag")
    parser.add_argument("--project", default="gcs-fuse-test", help="GCP Project")
    parser.add_argument("--iterations", type=int, default=2, help="Number of iterations")
    parser.add_argument("--reset", action="store_true", help="Reset saved state and start a fresh run")
    parser.add_argument("--smoke-mode", action="store_true", help="Run orchestrator in fast smoke test mode")
    
    args = parser.parse_args()
    if args.smoke_mode and args.iterations == 2:
        args.iterations = 1
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
            elif t.get("type") == "gke":
                required_keys.extend(["cluster_name", "location"])
            missing = [k for k in required_keys if k not in t]
            if missing:
                raise ValueError(f"Target '{t.get('name', 'unknown')}' is missing required fields: {', '.join(missing)}")
            if not all(c.isalnum() or c in '-_' for c in t["name"]):
                raise ValueError(f"Target name '{t['name']}' is invalid. Only alphanumeric characters, dashes, and underscores are allowed.")
    except Exception as e:
        print(f"Error parsing configuration file {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    for t in targets:
        try:
            validate_colocation(t, PROJECT_ID)
        except Exception as e:
            print(f"Validation failed for target '{t.get('name', 'unknown')}': {e}", file=sys.stderr)
            sys.exit(1)

    state = load_state(targets)
    print(f"Current State: {json.dumps(state, indent=2)}")

    # Validate that required local files exist before starting
    required_files = [NPI_PY_PATH, NPI_GKE_PY_PATH, os.path.join(REPO_DIR, "npi_job_spec.yaml")]
    for path in required_files:
        if not os.path.exists(path):
            print(f"Error: Required local file not found: {path}", file=sys.stderr)
            sys.exit(1)

    # Task 6: Pre-truncate BigQuery tables once before parallel target execution threads start
    try:
        from google.cloud import bigquery
        bq_client = bigquery.Client(project=PROJECT_ID)
        truncated_tables = set()
        for t in targets:
            raw_dataset = t["dataset"]
            if raw_dataset.endswith("_regional"):
                base_dataset = raw_dataset[:-len("_regional")]
            elif raw_dataset.endswith("_zonal"):
                base_dataset = raw_dataset[:-len("_zonal")]
            else:
                base_dataset = raw_dataset
            dataset_id = f"{base_dataset}_regional"

            bench_list = args.benchmarks.split() if isinstance(args.benchmarks, str) else args.benchmarks
            for b_name in bench_list:
                table_id = f"fio_{b_name}"
                full_table_id = f"{PROJECT_ID}.{dataset_id}.{table_id}"
                if full_table_id not in truncated_tables:
                    try:
                        bq_client.get_table(full_table_id)
                        print(f"[Orchestrator] Pre-truncating BQ table safely before parallel runs: {full_table_id}")
                        bq_client.query(f"TRUNCATE TABLE `{full_table_id}`").result()
                    except Exception:
                        pass
                    truncated_tables.add(full_table_id)
    except Exception as e:
        print(f"[Orchestrator] Note: Pre-run BQ truncation skipped or BQ client not available locally: {e}")

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
