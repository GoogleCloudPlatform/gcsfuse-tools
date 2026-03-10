# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""VM management operations"""

import os
import shlex
import subprocess
import time
from datetime import datetime, timedelta
from . import gcs, gcloud_utils


def resolve_executor_vms(executor_vm, zone, project, include_template=False):
    """
    Resolves the executor_vm into a list of running VM names.
    The executor_vm can be a single VM name or a Managed Instance Group name.
    Assumes machines are up and running, otherwise returns failure.
    """
    format_string = 'value(name,instanceTemplate)' if include_template else 'value(name)'
    # 1. Try to describe as a single instance
    try:
        # We use describe to check if the name exists as a VM and get its status
        cmd = [
            'gcloud', 'compute', 'instances', 'describe', executor_vm,
            f'--zone={zone}', f'--project={project}',
            '--format=value(status)'
            # We don't fetch template here as it's less common for single VMs
        ]
        result = gcloud_utils.run_gcloud_command(cmd, check=True, capture_output=True)
        status = result.stdout.strip()
        if status == 'RUNNING':
            print(f"Executor VM identified as a single running VM: {executor_vm}")
            # For single VM, we don't have an easy template name, return a simplified structure
            if include_template:
                return [{'name': executor_vm, 'template': 'unknown-single-vm'}]
            return [executor_vm]
        else:
            print(f"Error: Executor VM '{executor_vm}' exists but is in status '{status}'. Expected 'RUNNING'.")
            return []
    except Exception:
        # Not a single VM or describe failed, proceed to check if it's a MIG
        pass

    # 2. Try to list instances from a Managed Instance Group
    try:
        vms = gcloud_utils.gcloud_compute_instance_group_list(executor_vm, zone, project, filter_status='RUNNING', include_template=include_template)
        if vms:
            print(f"Executor VM identified as a MIG '{executor_vm}' with {len(vms)} running VMs.")
            return vms
        else:
            print(f"Warning: No running VMs found in executor_vm instance group '{executor_vm}'.")
            return []
    except Exception:
        # Neither a VM nor a MIG
        pass

    raise ValueError(f"Executor VM '{executor_vm}' is neither a running VM nor a valid Managed Instance Group in zone '{zone}'")


def run_worker_script(vm_name, zone, project, script_path, benchmark_id, artifacts_bucket):
    """Execute worker script on VM via gcloud ssh"""
    
    # Convert to absolute path
    script_path = os.path.abspath(script_path)
    
    # Upload worker scripts first to ensure they exist in /tmp
    script_dir = os.path.dirname(script_path)
    workers = ['setup.sh', 'monitor.sh', 'build.sh', 'runner.sh', 'worker.sh']
    for worker in workers:
        local_worker_file = os.path.join(script_dir, worker)
        if os.path.exists(local_worker_file):
            try:
                gcloud_utils.gcloud_compute_scp(
                    local_worker_file,
                    f'{vm_name}:~/{worker}',
                    zone=zone,
                    project=project,
                    internal_ip=True,
                    check=True
                )
            except Exception as e:
                print(f"Failed to upload script to {vm_name}: {e}")
                raise
    
    log_file = f"worker_{benchmark_id}.log"
    remote_script = f"./{os.path.basename(script_path)}"

    # Use shlex.quote to prevent command injection vulnerabilities
    quoted_script = shlex.quote(remote_script)
    quoted_id = shlex.quote(benchmark_id)
    quoted_bucket = shlex.quote(artifacts_bucket)
    quoted_log = shlex.quote(log_file)
    
    exec_command = f'nohup bash {quoted_script} {quoted_id} {quoted_bucket} > {quoted_log} 2>&1 &'

    try:
        gcloud_utils.gcloud_compute_ssh(
            vm_name,
            zone=zone,
            project=project,
            command=exec_command,
            internal_ip=True,
            check=True,
            capture_output=True,
            text=True
        )
    except Exception as e:
        print(f"Failed to execute script on {vm_name}: {e}")
        raise


def fetch_worker_logs(vm_name, benchmark_id, artifacts_bucket, lines=50):
    """Fetch and display worker logs from GCS"""
    log_path = f"gs://{artifacts_bucket}/{benchmark_id}/logs/{vm_name}/worker.log"
    
    try:
        result = gcloud_utils.run_gcloud_command(['gcloud', 'storage', 'cat', log_path], capture_output=True, text=True, timeout=10, check=False)
        
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


def _get_vm_test_progress(benchmark_id, artifacts_bucket):
    """Poll GCS to count completed tests per VM based on result directories."""
    path = f"gs://{artifacts_bucket}/{benchmark_id}/results/**/job.fio"
    cmd = ['gcloud', 'storage', 'ls', path]
    result = gcloud_utils.run_gcloud_command(cmd, capture_output=True, text=True, check=False)
    
    counts = {}
    if result.returncode == 0:
        prefix = f"gs://{artifacts_bucket}/{benchmark_id}/results/"
        for line in result.stdout.splitlines():
            if line.startswith(prefix):
                # Extract VM name: it's the first part after the prefix
                relative_path = line[len(prefix):]
                vm_name = relative_path.split('/')[0]
                counts[vm_name] = counts.get(vm_name, 0) + 1
    return counts


def wait_for_completion(vms, benchmark_id, artifacts_bucket, poll_interval=30, timeout=7200, expected_counts=None):
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
        # Get granular progress by counting uploaded result files
        progress_counts = _get_vm_test_progress(benchmark_id, artifacts_bucket)

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
        status_msg = f"  Progress: {len(completed_vms)}/{len(vms)} VMs finished"
        if failed_vms:
            status_msg += f", {len(failed_vms)} failed"
            
        if in_progress_vms:
            details = []
            for vm in sorted(in_progress_vms):
                done = progress_counts.get(vm, 0)
                total = expected_counts.get(vm, "?") if expected_counts else "?"
                details.append(f"{vm}: {done}/{total}")
            status_msg += f" | In-progress: {', '.join(details)}"
        
        print(status_msg)
        time.sleep(poll_interval)
    
    # Timeout reached - trigger cancellation
    print(f"\n⚠ Timeout reached after {timeout}s. Completed: {len(completed_vms)}/{len(vms)}")
    
    in_progress_vms = set(vms) - completed_vms - failed_vms
    if in_progress_vms:
        print(f"Triggering cancellation for in-progress VMs: {', '.join(sorted(in_progress_vms))}")
        
        # Create cancellation flag
        cancel_path = f"gs://{artifacts_bucket}/{benchmark_id}/cancel"
        gcloud_utils.run_gcloud_command(['gcloud', 'storage', 'cp', '-', cancel_path], input='timeout', capture_output=True)
        
        print(f"Cancellation flag created. Waiting 30s for workers to detect and shutdown...")
        time.sleep(30)
        
        print("Workers should have detected cancellation and stopped gracefully.")
    
    return False
