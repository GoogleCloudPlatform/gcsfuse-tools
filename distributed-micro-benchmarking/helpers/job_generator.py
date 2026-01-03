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


def distribute_tests(test_cases, vms):
    """Distribute test cases evenly across VMs"""
    num_vms = len(vms)
    tests_per_vm = len(test_cases) // num_vms
    remaining = len(test_cases) % num_vms
    
    distribution = {}
    start_idx = 0
    
    for i, vm in enumerate(vms):
        count = tests_per_vm + (1 if i < remaining else 0)
        end_idx = start_idx + count
        
        vm_tests = [tc['test_id'] for tc in test_cases[start_idx:end_idx]]
        distribution[vm] = vm_tests
        start_idx = end_idx
    
    return distribution


def create_job_spec(vm_name, benchmark_id, test_ids, bucket, artifacts_bucket, iterations):
    """Create job specification for a VM"""
    return {
        "vm_name": vm_name,
        "benchmark_id": benchmark_id,
        "test_ids": test_ids,
        "bucket": bucket,
        "artifacts_bucket": artifacts_bucket,
        "iterations": iterations,
        "total_tests": len(test_ids),
        "total_runs": len(test_ids) * iterations
    }
