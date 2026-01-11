"""Result aggregation from distributed VMs"""

import json
import glob
import tempfile
import os
import subprocess
from . import gcs


def aggregate_results(benchmark_id, artifacts_bucket, vms, mode="single-config"):
    """Aggregate results from all VMs.
    
    Downloads results from gs://<artifacts_bucket>/<benchmark_id>/results/<vm>/ for each VM.
    Each VM's results directory contains:
    - manifest.json: List of tests with status and metadata
    - test-<id>/: Directory per test with FIO JSON outputs and resource metrics
    
    In multi-config mode, test_key is matrix_id (unique across config×test combinations).
    In single-config mode, test_key is test_id (can be same across VMs if distributed).
    
    Returns dict mapping test_key -> aggregated metrics (bandwidth, CPU, memory, etc).
    """
    all_metrics = {}
    successful_vms = 0
    failed_vms = []
    
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
                    raise Exception(f"gcloud command failed: {result.stderr}")
            except Exception as e:
                print(f"Warning: Could not download results for {vm}: {e}")
                failed_vms.append(vm)
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
                    successful_vms += 1
    
    # Print summary
    if failed_vms:
        print(f"\nWarning: Failed to get results from {len(failed_vms)} VM(s): {', '.join(failed_vms)}")
    print(f"Successfully aggregated results from {successful_vms}/{len(vms)} VMs")
    
    return all_metrics


def parse_test_results(
    test_dir: str,
    test_info: Dict[str, Any],
    mode: str = "single-config"
) -> Dict[str, Any]:
    """Parse FIO results from a test directory.
    
    Args:
        test_dir: Path to test directory containing FIO output files
        test_info: Test metadata from manifest
        mode: Benchmark mode ('single-config' or 'multi-config')
        
    Returns:
        Dictionary containing aggregated metrics and parameters
    """
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
