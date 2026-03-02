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

"""
Distributed Micro-Benchmarking Orchestrator

Coordinates distributed benchmark execution across multiple VMs by treating 
all runs as a test matrix (Cartesian product of configs and test cases).
"""

import argparse
import json
import sys
import os
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from helpers import gcs, vm_manager, job_generator, result_aggregator, report_generator
import cancel


def parse_args():
    parser = argparse.ArgumentParser(description="Orchestrate distributed GCSFuse benchmarks")

    parser.add_argument('--test-csv', type=str, required=True, help='Path to test cases CSV')
    parser.add_argument('--configs-csv', type=str, default=None, help='Path to configs CSV (optional)')
    parser.add_argument('--fio-job-file', type=str, required=True, help='Path to FIO job template file')
    parser.add_argument('--iterations', type=int, required=True, help='Iterations per test')
    parser.add_argument('--separate-configs', action='store_true', help='Generate separate reports per config')

    parser.add_argument('--project', type=str, required=True, help='GCP project')
    parser.add_argument('--zone', type=str, required=True, help='GCP compute zone (where MIG/VM is located)')
    parser.add_argument('--executor-vm', type=str, required=True, help='Existing VM name or Managed Instance Group name')

    parser.add_argument('--artifacts-bucket', type=str, required=True, help='GCS bucket for artifacts')
    parser.add_argument('--test-data-bucket', type=str, required=True, help='GCS bucket for testing')

    parser.add_argument('--gcsfuse-commit', type=str, default='master', help='GCSFuse branch/commit (used if no configs-csv)')
    parser.add_argument('--gcsfuse-mount-args', type=str, default='', help='GCSFuse mount arguments (used if no configs-csv)')

    parser.add_argument('--benchmark-id', type=str, required=True, help='Unique benchmark ID')
    parser.add_argument('--poll-interval', type=int, default=30, help='Polling interval in seconds')
    parser.add_argument('--timeout', type=int, default=7200, help='Timeout in seconds')
    parser.add_argument('--run-name', type=str, default=None, help='Descriptive run name (default: benchmark-id)')
    parser.add_argument('--report-name', type=str, default='combined_report.csv', help='Report file name')

    parser.add_argument('--single-thread-vm-type', type=str, default=None, help='Identifier in instance template name for single-threaded VMs (e.g., n2-standard-32)')
    parser.add_argument('--multi-thread-vm-type', type=str, default=None, help='Identifier in instance template name for multi-threaded VMs (e.g., c4-standard-192)')
    return parser.parse_args()


def main():
    args = parse_args()
    
    try:
        run_benchmark(args)
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
        print("Triggering cancellation on workers...")
        cancel.create_cancel_flag(args.benchmark_id, args.artifacts_bucket)
        sys.exit(130)
    except Exception as e:
        print(f"\nERROR: Benchmark failed: {e}")
        sys.exit(1)


def run_benchmark(args):
    print(f"========== Distributed Benchmark Orchestrator ==========")
    print(f"Benchmark ID: {args.benchmark_id}")
    print(f"Executor VMs: {args.executor_vm}")
    
    # 0. Create results directory with benchmark ID
    results_dir = f"results/{args.benchmark_id}"
    os.makedirs(results_dir, exist_ok=True)
    print(f"Results directory: {results_dir}")

    # Extract basenames to preserve original filenames
    test_csv_name = os.path.basename(args.test_csv)
    fio_job_name = os.path.basename(args.fio_job_file)
    configs_csv_name = os.path.basename(args.configs_csv) if args.configs_csv else "configs.csv"

    # Save input files to preserve configuration
    shutil.copy(args.test_csv, f"{results_dir}/{test_csv_name}")
    if args.configs_csv:
        shutil.copy(args.configs_csv, f"{results_dir}/{configs_csv_name}")
    shutil.copy(args.fio_job_file, f"{results_dir}/{fio_job_name}")
    

    # 2. Load test cases and configs
    test_cases = job_generator.load_test_cases(args.test_csv)
    if args.configs_csv:
        configs = job_generator.load_configs(args.configs_csv)
    else:
        # Create a single default config from command line arguments
        configs = [{
            'config_id': 0,
            'label': 'default',
            'commit': args.gcsfuse_commit,
            'mount_args': args.gcsfuse_mount_args
        }]
        print(f"Running with default config (commit: {args.gcsfuse_commit})")
    print(f"Loaded {len(test_cases)} test cases")
    print(f"Loaded {len(configs)} config variations")
    
    # 3. Generate test matrix and distribute
    test_matrix = job_generator.generate_test_matrix(test_cases, configs)
    print(f"Generated test matrix: {len(test_matrix)} total tests ({len(configs)} configs × {len(test_cases)} tests)")

    # 1. Resolve VMs and distribute tests based on provided flags
    if args.single_thread_vm_type and args.multi_thread_vm_type:
        print("\nDistributing tests by VM type...")
        # Fetch VMs with their instance templates
        vms_with_templates = vm_manager.resolve_executor_vms(args.executor_vm, args.zone, args.project, include_template=True)
        vms = [vm_info['name'] for vm_info in vms_with_templates]

        # Classify VMs based on the provided identifiers
        single_thread_vms = [vm['name'] for vm in vms_with_templates if args.single_thread_vm_type in vm.get('template', '')]
        multi_thread_vms = [vm['name'] for vm in vms_with_templates if args.multi_thread_vm_type in vm.get('template', '')]

        print(f"Found {len(vms)} total running VMs: {', '.join(vms)}")
        print(f"  - Single-threaded VMs ({args.single_thread_vm_type}): {len(single_thread_vms)}")
        print(f"  - Multi-threaded VMs ({args.multi_thread_vm_type}): {len(multi_thread_vms)}")

        distribution = job_generator.distribute_tests_by_type(test_matrix, single_thread_vms, multi_thread_vms)
    else:
        print("\nDistributing tests evenly across all VMs (no VM types specified)...")
        # Fetch only VM names
        vms = vm_manager.resolve_executor_vms(args.executor_vm, args.zone, args.project)
        print(f"Found {len(vms)} running VMs: {', '.join(vms)}")
        distribution = job_generator.distribute_tests(test_matrix, vms)

    if not vms:
        print(f"ERROR: No running VMs found for executor-vm '{args.executor_vm}'")
        sys.exit(1)

    # Save run configuration metadata
    run_config = {
        "timestamp": datetime.now().isoformat(),
        "benchmark_id": args.benchmark_id,
        "num_vms": len(vms),
        "vm_names": vms,
        "num_tests": len(test_cases),
        "num_configs": len(configs) if configs else 1,
        "iterations": args.iterations,
        "bucket": args.test_data_bucket,
        "artifacts_bucket": args.artifacts_bucket,
        "instance_group": args.executor_vm,
        "zone": args.zone,
        "project": args.project
    }
    with open(f"{results_dir}/run-config.json", 'w') as f:
        json.dump(run_config, f, indent=2)
    print(f"✓ Run configuration saved to {results_dir}/run-config.json")
    print(f"\nTest Distribution:")
    for vm_name, tests in distribution.items():
        print(f"  {vm_name}: {len(tests)} tests")
    
    # 4. Create config dict and upload to GCS
    config = {
        'iterations': args.iterations,
        'bucket': args.test_data_bucket,
        'separate_configs': args.separate_configs,
        'test_filename': test_csv_name,
        'job_filename': fio_job_name
    }
    base_path = f"gs://{args.artifacts_bucket}/{args.benchmark_id}"
    config_path = f"{base_path}/config.json"
    gcs.upload_json(config, config_path)
    print(f"Uploaded config: iterations={args.iterations}, bucket={args.artifacts_bucket}")
    gcs.upload_test_cases(args.test_csv, f"{base_path}/{test_csv_name}")
    print(f"Uploaded test cases to: {base_path}/{test_csv_name}")
    # Upload configs.csv
    if args.configs_csv:
        configs_dest = f"{base_path}/{configs_csv_name}"
        gcs.upload_test_cases(args.configs_csv, configs_dest)
        print(f"Uploaded configs to: {configs_dest}")
    gcs.upload_fio_job_file(args.fio_job_file, f"{base_path}/{fio_job_name}")
    print(f"Uploaded FIO job file to: {base_path}/{fio_job_name}")
    
    # 5. Generate and upload job files for each VM (in parallel)
    active_vms = [] 
    jobs_to_upload = []

    # Calculate total test cases for modulo arithmetic
    num_test_cases = len(test_cases)
    for vm_name, test_entries in distribution.items():
        if not test_entries:
            print(f"Skipping {vm_name}: No tests assigned")
            continue
        active_vms.append(vm_name)

        for entry in test_entries:
            if isinstance(entry, dict) and 'matrix_id' in entry and num_test_cases > 0:
                # Map global ID back to [0, num_test_cases-1]
                entry['test_id'] = entry['matrix_id'] % num_test_cases
        
        job = job_generator.create_job_spec(
            vm_name=vm_name,
            benchmark_id=args.benchmark_id,
            test_entries=test_entries,
            bucket=args.test_data_bucket,
            artifacts_bucket=args.artifacts_bucket,
            iterations=args.iterations,
        )
        job_path = f"{base_path}/jobs/{vm_name}.json"
        jobs_to_upload.append((vm_name, job, job_path, len(test_entries)))
    
    # Upload jobs in parallel
    def upload_job(job_info):
        vm_name, job, job_path, num_tests = job_info
        gcs.upload_json(job, job_path)
        return vm_name, num_tests
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(upload_job, job_info) for job_info in jobs_to_upload]
        for future in as_completed(futures):
            vm_name, num_tests = future.result()
            print(f"Uploaded job for {vm_name}: {num_tests} tests, {num_tests * args.iterations} total runs")
    
    if not active_vms:
        print("\nERROR: No VMs have test assignments")
        sys.exit(1)
    print(f"\nActive VMs: {len(active_vms)}/{len(vms)}")
    
    # 6. Trigger VMs to start execution (in parallel)
    print(f"\nTriggering VMs...")
    worker_script = "workers/worker.sh"
    
    def trigger_vm(vm_name):
        vm_manager.run_worker_script(
            vm_name,
            args.zone,
            args.project,
            worker_script,
            args.benchmark_id,
            args.artifacts_bucket
        )
        return vm_name
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(trigger_vm, vm_name) for vm_name in active_vms]
        for future in as_completed(futures):
            vm_name = future.result()
            print(f"  Started {vm_name}")
    
    # 7. Monitor progress by polling manifests
    print(f"\nMonitoring progress (polling every {args.poll_interval}s)...")
    completed = vm_manager.wait_for_completion(
        vms=active_vms,
        benchmark_id=args.benchmark_id,
        artifacts_bucket=args.artifacts_bucket,
        poll_interval=args.poll_interval,
        timeout=args.timeout
    )
    if not completed:
        print("\nWARNING: Not all active VMs completed successfully")
        print("Continuing with report generation for successful VMs...")
    else:
        print(f"\n✓ All VMs completed successfully!")
    
    # 8. Aggregate results
    print(f"\nAggregating results...")
    metrics = result_aggregator.aggregate_results(
        benchmark_id=args.benchmark_id,
        artifacts_bucket=args.artifacts_bucket,
        vms=active_vms
    )
    if not metrics:
        print("\nERROR: No test results collected from any VM")
        sys.exit(1)
    
    # 9. Generate report
    report_file = f"{results_dir}/{args.report_name}"
    report_generator.generate_report(metrics, report_file, separate_configs=args.separate_configs)
    if args.separate_configs:
        print(f"\n✓ Reports generated in {results_dir}/")
    else:
        print(f"\n✓ Report generated: {report_file}")
    
    # Update 'latest' symlink
    latest_link = "results/latest"
    if os.path.islink(latest_link):
        os.unlink(latest_link)
    elif os.path.exists(latest_link):
        shutil.rmtree(latest_link)
    os.symlink(args.benchmark_id, latest_link)
    
    print(f"\n========== Benchmark Complete ==========")
    print(f"Results saved to: {results_dir}/")
    print(f"  - Input files: {test_csv_name}, {configs_csv_name if args.configs_csv else ''}, {fio_job_name}")
    print(f"  - Report: {args.report_name}")
    print(f"  - Latest: results/latest/ ")
    
    # Exit with error code if some VMs failed
    if not completed:
        sys.exit(1)


if __name__ == '__main__':
    main()
