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
    
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    vms = [vm.strip() for vm in result.stdout.strip().split('\n') if vm.strip()]
    return vms


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
        f'--project={project}',
        '--internal-ip'
    ]
    
    try:
        result = subprocess.run(upload_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to upload script to {vm_name}:")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        raise
    
    # Execute script with benchmark_id and artifacts_bucket as arguments
    exec_cmd = [
        'gcloud', 'compute', 'ssh', vm_name,
        f'--zone={zone}',
        f'--project={project}',
        '--internal-ip',
        '--command',
        f'bash {remote_script} {benchmark_id} {artifacts_bucket} > /tmp/worker.log 2>&1 &'
    ]
    
    try:
        subprocess.run(exec_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to execute script on {vm_name}:")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        raise


def wait_for_completion(vms, benchmark_id, artifacts_bucket, poll_interval=30, timeout=7200):
    """Wait for all VMs to complete by monitoring manifests"""
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
                elif manifest.get('status') == 'failed':
                    failed_vms.add(vm)
                    print(f"  ✗ {vm} failed")
        
        # Check if done
        if len(completed_vms) + len(failed_vms) == len(vms):
            if failed_vms:
                print(f"\nCompleted: {completed_vms}")
                print(f"Failed: {failed_vms}")
                return False
            return True
        
        print(f"  Progress: {len(completed_vms)}/{len(vms)} completed, {len(failed_vms)} failed")
        time.sleep(poll_interval)
    
    print(f"\nTimeout reached. Completed: {len(completed_vms)}/{len(vms)}")
    return False
