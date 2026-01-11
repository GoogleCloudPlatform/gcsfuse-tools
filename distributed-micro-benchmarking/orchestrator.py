#!/usr/bin/env python3
"""
Distributed Micro-Benchmarking Orchestrator

Coordinates distributed benchmark execution across multiple VMs.
"""

import argparse
import json
import sys
from helpers import gcs, vm_manager, job_generator, result_aggregator, report_generator


def parse_args():
    parser = argparse.ArgumentParser(description="Orchestrate distributed GCSFuse benchmarks")
    parser.add_argument('--benchmark-id', type=str, required=True, help='Unique benchmark ID')
    parser.add_argument('--instance-group', type=str, required=True, help='Managed instance group name')
    parser.add_argument('--zone', type=str, required=True, help='GCP zone')
    parser.add_argument('--project', type=str, required=True, help='GCP project')
    parser.add_argument('--artifacts-bucket', type=str, required=True, help='GCS bucket for artifacts')
    parser.add_argument('--test-csv', type=str, required=True, help='Path to test cases CSV')
    parser.add_argument('--configs-csv', type=str, default=None, help='Path to configs CSV (optional)')
    parser.add_argument('--separate-configs', action='store_true', help='Generate separate reports per config')
    parser.add_argument('--fio-job-file', type=str, required=True, help='Path to FIO job template file')
    parser.add_argument('--bucket', type=str, required=True, help='GCS bucket for testing')
    parser.add_argument('--iterations', type=int, required=True, help='Iterations per test')
    parser.add_argument('--gcsfuse-commit', type=str, default='master', help='GCSFuse branch/commit (used if no configs-csv)')
    parser.add_argument('--gcsfuse-mount-args', type=str, default='', help='GCSFuse mount arguments (used if no configs-csv)')
    parser.add_argument('--poll-interval', type=int, default=30, help='Polling interval in seconds')
    parser.add_argument('--timeout', type=int, default=7200, help='Timeout in seconds')
    return parser.parse_args()


def main():
    args = parse_args()
    
    try:
        run_benchmark(args)
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nERROR: Benchmark failed: {e}")
        sys.exit(1)


def run_benchmark(args):
    print(f"========== Distributed Benchmark Orchestrator ==========")
    print(f"Benchmark ID: {args.benchmark_id}")
    print(f"Instance Group: {args.instance_group}")
    
    # 1. Get active VMs from instance group
    vms = vm_manager.get_running_vms(args.instance_group, args.zone, args.project)
    if not vms:
        print("ERROR: No running VMs found in instance group")
        sys.exit(1)
    
    print(f"\nFound {len(vms)} running VMs: {', '.join(vms)}")
    
    # 2. Load test cases and optionally configs
    test_cases = job_generator.load_test_cases(args.test_csv)
    print(f"Loaded {len(test_cases)} test cases")
    
    # Check if multi-config mode
    if args.configs_csv:
        configs = job_generator.load_configs(args.configs_csv)
        print(f"Loaded {len(configs)} config variations")
        
        # Generate test matrix (cartesian product)
        test_matrix = job_generator.generate_test_matrix(test_cases, configs)
        print(f"Generated test matrix: {len(test_matrix)} total tests ({len(configs)} configs × {len(test_cases)} tests)")
        
        distribution = job_generator.distribute_tests(test_matrix, vms, is_matrix=True)
        mode = "multi-config"
    else:
        # Single config mode (backwards compatible)
        print(f"Single config mode (commit: {args.gcsfuse_commit})")
        distribution = job_generator.distribute_tests(test_cases, vms, is_matrix=False)
        mode = "single-config"
    
    print(f"\nTest Distribution:")
    for vm_name, tests in distribution.items():
        print(f"  {vm_name}: {len(tests)} tests")
    
    # 3. Create config dict and upload to GCS
    config = {
        'mode': mode,
        'iterations': args.iterations,
        'bucket': args.bucket,
        'separate_configs': args.separate_configs
    }
    
    # Add single-config params if applicable
    if mode == "single-config":
        config['gcsfuse_commit'] = args.gcsfuse_commit
        config['gcsfuse_mount_args'] = args.gcsfuse_mount_args
    
    base_path = f"gs://{args.artifacts_bucket}/{args.benchmark_id}"
    
    config_path = f"{base_path}/config.json"
    gcs.upload_json(config, config_path)
    print(f"Uploaded config: mode={mode}, iterations={args.iterations}, bucket={args.bucket}")
    
    gcs.upload_test_cases(args.test_csv, base_path)
    print(f"Uploaded test cases to: {base_path}/test-cases.csv")
    
    # Upload configs.csv if in multi-config mode
    if args.configs_csv:
        configs_dest = f"{base_path}/configs.csv"
        gcs.upload_test_cases(args.configs_csv, configs_dest)
        print(f"Uploaded configs to: {configs_dest}")
    
    gcs.upload_fio_job_file(args.fio_job_file, base_path)
    print(f"Uploaded FIO job file to: {base_path}/jobfile.fio")
    
    # 4. Generate and upload job files for each VM
    active_vms = []  # Track VMs with actual test assignments
    for vm_name, test_entries in distribution.items():
        if not test_entries:
            print(f"Skipping {vm_name}: No tests assigned")
            continue
            
        active_vms.append(vm_name)
        
        job = job_generator.create_job_spec(
            vm_name=vm_name,
            benchmark_id=args.benchmark_id,
            test_entries=test_entries,
            bucket=args.bucket,
            artifacts_bucket=args.artifacts_bucket,
            iterations=args.iterations,
            mode=mode
        )
        
        job_path = f"{base_path}/jobs/{vm_name}.json"
        gcs.upload_json(job, job_path)
        print(f"Uploaded job for {vm_name}: {len(test_entries)} tests, {len(test_entries) * args.iterations} total runs")
    
    if not active_vms:
        print("\nERROR: No VMs have test assignments")
        sys.exit(1)
    
    print(f"\nActive VMs: {len(active_vms)}/{len(vms)}")
    
    # 5. Trigger VMs to start execution
    print(f"\nTriggering VMs...")
    worker_script = "resources/worker.sh"
    for vm_name in active_vms:
        vm_manager.run_worker_script(vm_name, args.zone, args.project, worker_script, args.benchmark_id, args.artifacts_bucket)
        print(f"  Started {vm_name}")
    
    # 6. Monitor progress by polling manifests
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
    
    # 7. Aggregate results
    print(f"\nAggregating results...")
    metrics = result_aggregator.aggregate_results(
        benchmark_id=args.benchmark_id,
        artifacts_bucket=args.artifacts_bucket,
        vms=active_vms,
        mode=mode
    )
    
    if not metrics:
        print("\nERROR: No test results collected from any VM")
        sys.exit(1)
    
    # 8. Generate report
    report_file = f"results/{args.benchmark_id}_report.csv"
    report_generator.generate_report(metrics, report_file, mode=mode, separate_configs=args.separate_configs)
    
    if args.separate_configs:
        print(f"\nReports generated: results/{args.benchmark_id}_*.csv")
    else:
        print(f"\nReport generated: {report_file}")
    
    print(f"\n========== Benchmark Complete ==========")
    
    # Exit with error code if some VMs failed
    if not completed:
        sys.exit(1)


if __name__ == '__main__':
    main()
