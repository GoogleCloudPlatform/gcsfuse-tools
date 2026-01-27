"""VM management operations"""

import subprocess
import time
from datetime import datetime, timedelta
from . import gcs


def get_running_vms(instance_group, zone, project):
    """Get list of RUNNING VMs from instance group"""
    cmd = [
        'gcloud', 'compute', 'instance-groups', 'managed', 'list-instances',
        instance_group,
        f'--zone={zone}',
        f'--project={project}',
        '--filter=STATUS=RUNNING',
        '--format=value(NAME)'
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        vms = [vm.strip() for vm in result.stdout.strip().split('\n') if vm.strip()]
        return vms
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to list VMs in instance group '{instance_group}': {e.stderr}")


def run_worker_script(vm_name, zone, project, script_path, benchmark_id, artifacts_bucket):
    """Execute worker script on VM via gcloud ssh"""
    import os
    
    # Convert to absolute path
    script_path = os.path.abspath(script_path)
    
    # Create command to upload and execute script
    remote_script = f"/tmp/worker_{benchmark_id}.sh"
    
    # Upload script
    upload_cmd = [
        'gcloud', 'compute', 'scp',
        script_path,
        f'{vm_name}:{remote_script}',
        f'--zone={zone}',
        f'--project={project}'
    ]
    
    try:
        result = subprocess.run(upload_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to upload script to {vm_name}:")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        raise
    
    # Execute script with benchmark_id and artifacts_bucket as arguments
    # Logs will be written to /tmp/worker_<benchmark_id>.log and uploaded to GCS by worker.sh
    log_file = f"/tmp/worker_{benchmark_id}.log"
    exec_cmd = [
        'gcloud', 'compute', 'ssh', vm_name,
        f'--zone={zone}',
        f'--project={project}',
        '--internal-ip',
        '--command',
        f'nohup bash {remote_script} {benchmark_id} {artifacts_bucket} > {log_file} 2>&1 &'
    ]
    
    try:
        subprocess.run(exec_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to execute script on {vm_name}:")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        raise


def fetch_worker_logs(vm_name, benchmark_id, artifacts_bucket, lines=50):
    """Fetch and display worker logs from GCS"""
    log_path = f"gs://{artifacts_bucket}/{benchmark_id}/logs/{vm_name}/worker.log"
    
    try:
        cmd = ['gsutil', 'cat', log_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            log_lines = result.stdout.split('\n')
            if lines and len(log_lines) > lines:
                # Show first 10 and last (lines-10) lines
                print('\n'.join(log_lines[:10]))
                print(f"\n... [{len(log_lines) - lines} lines omitted] ...\n")
                print('\n'.join(log_lines[-(lines-10):]))
            else:
                print(result.stdout)
        else:
            print(f"Log not yet available for {vm_name}")
    except Exception as e:
        print(f"Could not fetch logs for {vm_name}: {e}")


def wait_for_completion(vms, benchmark_id, artifacts_bucket, poll_interval=30, timeout=7200):
    """Wait for all VMs to complete by monitoring manifests.
    
    Polls GCS for manifest.json files from each VM. Manifests indicate completion status:
    - 'completed': VM finished all assigned tests successfully
    - 'cancelled': User cancelled via cancel.py, partial results available
    - 'failed': VM encountered errors, logs fetched automatically
    
    Returns True if all VMs completed/cancelled, False if any failed or timeout occurred.
    """
    print(f"Waiting for {len(vms)} VMs to complete...")
    
    deadline = datetime.now() + timedelta(seconds=timeout)
    completed_vms = set()
    failed_vms = set()
    
    while datetime.now() < deadline:
        # Check for manifests
        for vm in vms:
            if vm in completed_vms or vm in failed_vms:
                continue
            
            manifest_path = f"gs://{artifacts_bucket}/{benchmark_id}/results/{vm}/manifest.json"
            manifest = gcs.download_json(manifest_path)
            
            if manifest:
                if manifest.get('status') == 'completed':
                    completed_vms.add(vm)
                    print(f"  ✓ {vm} completed - {manifest.get('total_tests', 0)} tests")
                elif manifest.get('status') == 'cancelled':
                    completed_vms.add(vm)
                    print(f"  ⚠ {vm} cancelled - {len(manifest.get('tests', []))} tests completed")
                elif manifest.get('status') == 'failed':
                    failed_vms.add(vm)
                    print(f"  ✗ {vm} failed")
                    print(f"\nLogs from {vm}:")
                    print("=" * 80)
                    fetch_worker_logs(vm, benchmark_id, artifacts_bucket, lines=100)
                    print("=" * 80)
        
        # Check if done
        if len(completed_vms) + len(failed_vms) == len(vms):
            if failed_vms:
                print(f"\nCompleted: {completed_vms}")
                print(f"Failed: {failed_vms}")
                return False
            return True
        
        # Calculate in-progress VMs
        in_progress_vms = set(vms) - completed_vms - failed_vms
        
        # Build status message
        status_msg = f"  Progress: {len(completed_vms)}/{len(vms)} completed"
        if failed_vms:
            status_msg += f", {len(failed_vms)} failed"
        if in_progress_vms:
            status_msg += f" | In-progress: {', '.join(sorted(in_progress_vms))}"
        
        print(status_msg)
        time.sleep(poll_interval)
    
    # Timeout reached - trigger cancellation
    print(f"\n⚠ Timeout reached after {timeout}s. Completed: {len(completed_vms)}/{len(vms)}")
    
    in_progress_vms = set(vms) - completed_vms - failed_vms
    if in_progress_vms:
        print(f"Triggering cancellation for in-progress VMs: {', '.join(sorted(in_progress_vms))}")
        
        # Create cancellation flag
        cancel_path = f"gs://{artifacts_bucket}/{benchmark_id}/cancel"
        cmd = ['gsutil', 'cp', '-', cancel_path]
        subprocess.run(cmd, input=b'timeout', capture_output=True)
        
        print(f"Cancellation flag created. Waiting 30s for workers to detect and shutdown...")
        time.sleep(30)
        
        print("Workers should have detected cancellation and stopped gracefully.")
    
    return False
