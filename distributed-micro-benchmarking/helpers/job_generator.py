"""Job specification generation and test distribution"""

import csv


def load_test_cases(csv_path):
    """Load test cases from CSV file"""
    test_cases = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            row['test_id'] = i
            test_cases.append(row)
    return test_cases


def load_configs(csv_path):
    """Load config variations from CSV file"""
    configs = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            row['config_id'] = i
            configs.append(row)
    return configs


def generate_test_matrix(test_cases, configs):
    """Generate cartesian product of configs × test_cases"""
    test_matrix = []
    matrix_id = 1
    
    for config in configs:
        for test_case in test_cases:
            # Spread test_case first, then override with matrix-specific IDs
            matrix_entry = {
                **test_case,
                'matrix_id': matrix_id,
                'config_id': config['config_id'],
                'test_id': test_case['test_id'],
                'commit': config['commit'],
                'mount_args': config['mount_args'],
                'config_label': config['label']
            }
            test_matrix.append(matrix_entry)
            matrix_id += 1
    
    return test_matrix


def distribute_tests(test_cases, vms, is_matrix=False):
    """Distribute test cases evenly across VMs
    
    Args:
        test_cases: List of test cases or matrix entries
        vms: List of VM names
        is_matrix: True if test_cases are matrix entries (already have matrix_id),
                   False if plain test cases (need test_id added)
    """
    num_vms = len(vms)
    tests_per_vm = len(test_cases) // num_vms
    remaining = len(test_cases) % num_vms
    
    distribution = {}
    start_idx = 0
    
    for i, vm in enumerate(vms):
        count = tests_per_vm + (1 if i < remaining else 0)
        end_idx = start_idx + count
        
        vm_tests = test_cases[start_idx:end_idx]
        
        # For single-config mode, test_id should already be present from load_test_cases
        # No need to reassign it - just use the tests as-is
        
        distribution[vm] = vm_tests
        start_idx = end_idx
    
    return distribution


def create_job_spec(vm_name, benchmark_id, test_entries, bucket, artifacts_bucket, iterations, mode="single-config"):
    """Create job specification for a VM
    
    test_entries can be either:
    - List of test_case dicts (single config mode) - will be converted to test_ids
    - List of matrix entries with config info (multi config mode) - stored as test_entries
    """
    job_spec = {
        "vm_name": vm_name,
        "benchmark_id": benchmark_id,
        "bucket": bucket,
        "artifacts_bucket": artifacts_bucket,
        "iterations": iterations,
        "total_tests": len(test_entries),
        "total_runs": len(test_entries) * iterations
    }
    
    # In single-config mode, use test_ids; in multi-config mode, use test_entries
    if mode == "single-config":
        # Extract test IDs from test entries
        job_spec["test_ids"] = [entry['test_id'] for entry in test_entries]
    else:
        # Multi-config: store full test entries with config info
        job_spec["test_entries"] = test_entries
    
    return job_spec
