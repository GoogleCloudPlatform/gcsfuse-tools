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

"""Result aggregation from distributed VMs"""

import json
import glob
import tempfile
import os
from . import gcs, gcloud_utils


def _avg(values):
    """Calculate average of non-empty list, return 0 if empty"""
    return sum(values) / len(values) if values else 0


def _extract_latency_metrics(job):
    """Extract read latency metrics from FIO job data"""
    if 'read' not in job or not job['read'].get('bw'):
        return None
    
    lat_metrics = {'bw': job['read']['bw']}
    
    # clat_ns stores values in MICROSECONDS (despite the name)
    # lat_ns stores values in NANOSECONDS
    # We prefer clat_ns (completion latency) over lat_ns (total latency)
    if 'clat_ns' in job['read']:
        lat_data = job['read']['clat_ns']
        divisor = 1000.0  # µs to ms
    elif 'lat_ns' in job['read']:
        lat_data = job['read']['lat_ns']
        divisor = 1000000.0  # ns to ms
    else:
        return lat_metrics
    
    # Extract basic stats
    for key in ['min', 'max', 'mean', 'stddev']:
        if key in lat_data:
            lat_metrics[key] = lat_data[key] / divisor
    
    # Extract percentiles
    if 'percentile' in lat_data:
        percentiles = lat_data['percentile']
        for pct, pct_key in [('50.000000', 'p50'), ('90.000000', 'p90'), ('99.000000', 'p99')]:
            if pct in percentiles:
                lat_metrics[pct_key] = percentiles[pct] / divisor
    
    return lat_metrics


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
                gcloud_utils.gcloud_storage_cp(f"{vm_path}/*", local_vm_dir, recursive=True, retries=1, check=True)
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


def parse_test_results(test_dir, test_info, mode="single-config"):
    """Parse FIO results from a test directory"""
    fio_files = glob.glob(os.path.join(test_dir, "fio_output_*.json"))
    
    read_bws = []
    write_bws = []
    lat_lists = {key: [] for key in ['min', 'max', 'mean', 'stddev', 'p50', 'p90', 'p99']}
    
    for fio_file in fio_files:
        with open(fio_file, 'r') as f:
            data = json.load(f)
            
            for job in data.get('jobs', []):
                # Extract read metrics
                lat_metrics = _extract_latency_metrics(job)
                if lat_metrics:
                    read_bws.append(lat_metrics['bw'])
                    for key in lat_lists:
                        if key in lat_metrics:
                            lat_lists[key].append(lat_metrics[key])
                
                # Extract write metrics
                if 'write' in job and job['write'].get('bw'):
                    write_bws.append(job['write']['bw'])
    
    # Build result dict
    result = {
        'test_params': test_info.get('params', {}),
        'read_bw_mbps': _avg(read_bws) / 1000.0,
        'write_bw_mbps': _avg(write_bws) / 1000.0,
        'read_lat_min_ms': _avg(lat_lists['min']),
        'read_lat_max_ms': _avg(lat_lists['max']),
        'read_lat_avg_ms': _avg(lat_lists['mean']),
        'read_lat_stddev_ms': _avg(lat_lists['stddev']),
        'read_lat_p50_ms': _avg(lat_lists['p50']),
        'read_lat_p90_ms': _avg(lat_lists['p90']),
        'read_lat_p99_ms': _avg(lat_lists['p99']),
        'iterations': len(fio_files)
    }
    
    # In multi-config mode, include matrix_id and test_id
    if mode == "multi-config":
        result['matrix_id'] = test_info.get('matrix_id', test_info['test_id'])
        result['test_id'] = test_info.get('test_id')
    
    return result