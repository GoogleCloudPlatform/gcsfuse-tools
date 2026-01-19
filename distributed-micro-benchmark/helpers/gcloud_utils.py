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

"""Unified gcloud command execution utilities"""

import subprocess
import time


def run_gcloud_command(cmd, retries=1, retry_delay=2, check=False, capture_output=True, text=True, **kwargs):
    """Execute a gcloud command with optional retry logic.
    
    Args:
        cmd: List of command components (e.g., ['gcloud', 'storage', 'cp', ...])
        retries: Number of retry attempts (1 = no retry, 3 = try 3 times)
        retry_delay: Seconds to wait between retries
        check: If True, raise CalledProcessError on non-zero exit code
        capture_output: If True, capture stdout/stderr
        text: If True, decode output as text
        **kwargs: Additional arguments passed to subprocess.run()
        
    Returns:
        subprocess.CompletedProcess object
        
    Raises:
        Exception: If command fails after all retries and check=True
    """
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=capture_output, text=text, **kwargs)
        
        if result.returncode == 0:
            return result
        
        if attempt < retries - 1:
            time.sleep(retry_delay)
    
    if check:
        raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
    
    return result


def gcloud_storage_cp(source, dest, recursive=False, retries=3, check=True):
    """Copy files to/from GCS"""
    cmd = ['gcloud', 'storage', 'cp']
    if recursive:
        cmd.append('-r')
    cmd.extend([source, dest])
    
    return run_gcloud_command(cmd, retries=retries, check=check)


def gcloud_storage_ls(pattern, check=False):
    """List GCS objects matching a pattern"""
    cmd = ['gcloud', 'storage', 'ls', pattern]
    return run_gcloud_command(cmd, retries=1, check=check)


def gcloud_compute_ssh(vm_name, zone, project, command=None, internal_ip=True, check=True, **kwargs):
    """SSH to a compute instance"""
    cmd = ['gcloud', 'compute', 'ssh', vm_name, f'--zone={zone}', f'--project={project}']
    if internal_ip:
        cmd.append('--internal-ip')
    if command:
        cmd.extend(['--command', command])
    
    return run_gcloud_command(cmd, retries=1, check=check, **kwargs)


def gcloud_compute_scp(source, dest, zone, project, internal_ip=True, check=True):
    """Copy files to/from a compute instance"""
    cmd = ['gcloud', 'compute', 'scp', source, dest, f'--zone={zone}', f'--project={project}']
    if internal_ip:
        cmd.append('--internal-ip')
    
    return run_gcloud_command(cmd, retries=1, check=check)


def gcloud_compute_instance_group_list(instance_group, zone, project, filter_status='RUNNING'):
    """List VM names in a managed instance group"""
    cmd = [
        'gcloud', 'compute', 'instance-groups', 'managed', 'list-instances',
        instance_group,
        f'--zone={zone}',
        f'--project={project}',
        f'--filter=STATUS={filter_status}',
        '--format=value(NAME)'
    ]
    
    result = run_gcloud_command(cmd, retries=1, check=True)
    vms = [vm.strip() for vm in result.stdout.strip().split('\n') if vm.strip()]
    return vms
