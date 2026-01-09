"""Result aggregation from distributed VMs"""

import json
import glob
import tempfile
import os
import subprocess
from . import gcs


def aggregate_results(benchmark_id, artifacts_bucket, vms, mode="single-config"):
    """Aggregate results from all VMs
    
    Args:
        benchmark_id: Unique identifier for this benchmark run
        artifacts_bucket: GCS bucket for storing artifacts
        vms: List of VM names
        mode: "single-config" or "multi-config"
    """
    all_metrics = {}
    
    with tempfile.TemporaryDirectory() as tmpdir:
        for vm in vms:
            # Download VM results
            vm_path = f"gs://{artifacts_bucket}/{benchmark_id}/results/{vm}"
            local_vm_dir = os.path.join(tmpdir, vm)
            os.makedirs(local_vm_dir, exist_ok=True)
            
            try:
                # Download with wildcard to get contents
                cmd = ['gcloud', 'storage', 'cp', '-r', f"{vm_path}/*", local_vm_dir]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise Exception(f"Command {cmd} returned non-zero exit status {result.returncode}.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
            except Exception as e:
                print(f"Warning: Could not download results for {vm}: {e}")
                continue
            
            # Load manifest
            manifest_path = os.path.join(local_vm_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                print(f"Warning: No manifest found for {vm} at {manifest_path}")
                # List what we got
                print(f"  Contents: {os.listdir(local_vm_dir) if os.path.exists(local_vm_dir) else 'directory does not exist'}")
                continue
            
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            # Process each test result
            for test_info in manifest.get('tests', []):
                if test_info['status'] != 'success':
                    continue
                
                # In multi-config mode, use matrix_id as key; in single-config, use test_id
                if mode == "multi-config":
                    test_key = test_info.get('matrix_id', test_info['test_id'])
                    test_dir_name = f"test-{test_key}"
                else:
                    test_key = test_info['test_id']
                    test_dir_name = f"test-{test_key}"
                
                # Parse FIO results for this test
                test_dir = os.path.join(local_vm_dir, test_dir_name)
                if os.path.exists(test_dir):
                    metrics = parse_test_results(test_dir, test_info, mode)
                    all_metrics[test_key] = metrics
    
    return all_metrics


def parse_test_results(test_dir, test_info, mode="single-config"):
    """Parse FIO results from a test directory"""
    fio_files = glob.glob(os.path.join(test_dir, "fio_output_*.json"))
    
    read_bws = []
    write_bws = []
    
    for fio_file in fio_files:
        with open(fio_file, 'r') as f:
            data = json.load(f)
            
            for job in data.get('jobs', []):
                if 'read' in job and job['read'].get('bw'):
                    read_bws.append(job['read']['bw'])
                if 'write' in job and job['write'].get('bw'):
                    write_bws.append(job['write']['bw'])
    
    # Build result dict
    result = {
        'test_params': test_info.get('params', {}),
        'read_bw_mbps': sum(read_bws) / len(read_bws) / 1000.0 if read_bws else 0,
        'write_bw_mbps': sum(write_bws) / len(write_bws) / 1000.0 if write_bws else 0,
        'iterations': len(fio_files)
    }
    
    # In multi-config mode, include matrix_id and test_id
    if mode == "multi-config":
        result['matrix_id'] = test_info.get('matrix_id', test_info['test_id'])
        result['test_id'] = test_info.get('test_id')
    
    return result
