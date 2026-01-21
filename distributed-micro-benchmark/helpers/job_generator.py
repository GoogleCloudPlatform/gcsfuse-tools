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

"""Job specification generation and test distribution"""

import csv


def _load_csv_with_id(csv_path, id_field):
    """Generic CSV loader that adds sequential ID field"""
    items = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            row[id_field] = i
            items.append(row)
    return items


def load_test_cases(csv_path):
    """Load test cases from CSV file"""
    return _load_csv_with_id(csv_path, 'test_id')


def load_configs(csv_path):
    """Load config variations from CSV file"""
    return _load_csv_with_id(csv_path, 'config_id')


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


def distribute_tests(test_cases, vms):
    # TODO: Add is_matrix case!
    """Distribute test cases evenly across VMs"""
    num_vms = len(vms)
    if not num_vms:
        if test_cases:
            raise ValueError("Cannot distribute tests to an empty list of VMs.")
        return {}
    tests_per_vm = len(test_cases) // num_vms
    remaining = len(test_cases) % num_vms
    
    distribution = {}
    start_idx = 0
    
    for i, vm in enumerate(vms):
        count = tests_per_vm + (1 if i < remaining else 0)
        end_idx = start_idx + count
        distribution[vm] = test_cases[start_idx:end_idx]
        start_idx = end_idx
    
    return distribution


def create_job_spec(vm_name, benchmark_id, test_entries, bucket, artifacts_bucket, iterations, mode="single-config"):
    """Create job specification for a VM"""
    total_tests = len(test_entries)
    
    job_spec = {
        "vm_name": vm_name,
        "benchmark_id": benchmark_id,
        "bucket": bucket,
        "artifacts_bucket": artifacts_bucket,
        "iterations": iterations,
        "total_tests": total_tests,
        "total_runs": total_tests * iterations,
    }

    if mode == "single-config":
      job_spec['test_ids'] = [entry['test_id'] for entry in test_entries]
    else:
      job_spec['test_entries'] = test_entries
    
    return job_spec
