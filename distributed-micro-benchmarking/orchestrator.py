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
    parser.add_argument('--test-csv', type=str, required=True, help='Path to test cases CSV')
    parser.add_argument('--fio-job-file', type=str, required=True, help='Path to FIO job template file')
    parser.add_argument('--bucket', type=str, required=True, help='GCS bucket for testing')
    parser.add_argument('--artifacts-bucket', type=str, required=True, help='GCS bucket for artifacts')
    parser.add_argument('--zone', type=str, required=True, help='GCP zone')
    parser.add_argument('--project', type=str, required=True, help='GCP project')
    parser.add_argument('--iterations', type=int, default=5, help='Iterations per test')
    parser.add_argument('--poll-interval', type=int, default=30, help='Polling interval in seconds')
    parser.add_argument('--timeout', type=int, default=7200, help='Timeout in seconds')
    return parser.parse_args()


def main():
    args = parse_args()
    
    print(f"========== Distributed Benchmark Orchestrator ==========")
    print(f"Benchmark ID: {args.benchmark_id}")
    print(f"Instance Group: {args.instance_group}")
    
    # 1. Get active VMs from instance group
    vms = vm_manager.get_running_vms(args.instance_group, args.zone, args.project)
    if not vms:
        print("ERROR: No running VMs found in instance group")
        sys.exit(1)
    
    print(f"\nFound {len(vms)} running VMs: {', '.join(vms)}")
    
    # 2. Load test cases and distribute
    test_cases = job_generator.load_test_cases(args.test_csv)
    print(f"Loaded {len(test_cases)} test cases")
    
    distribution = job_generator.distribute_tests(test_cases, vms)
    
    print(f"\nTest Distribution:")
    for vm_name, tests in distribution.items():
        print(f"  {vm_name}: {len(tests)} tests")
    
    # 3. Upload shared config and test cases to GCS
    base_path = f"gs://{args.artifacts_bucket}/{args.benchmark_id}"
    
    # Check if config already exists (uploaded by run.sh)
    config_path = f"{base_path}/config.json"
    config = gcs.download_json(config_path)
    if config:
        print(f"\nUsing existing config: GCSFuse commit={config.get('gcsfuse_commit', 'master')}")
    
    gcs.upload_test_cases(args.test_csv, base_path)
    print(f"Uploaded test cases to: {base_path}/test-cases.csv")
    
    gcs.upload_fio_job_file(args.fio_job_file, base_path)
    print(f"Uploaded FIO job file to: {base_path}/jobfile.fio")
    
    # 4. Generate and upload job files for each VM
    for vm_name, test_ids in distribution.items():
        job = job_generator.create_job_spec(
            vm_name=vm_name,
            benchmark_id=args.benchmark_id,
            test_ids=test_ids,
            bucket=args.bucket,
            artifacts_bucket=args.artifacts_bucket,
            iterations=args.iterations
        )
        
        job_path = f"{base_path}/jobs/{vm_name}.json"
        gcs.upload_json(job, job_path)
        print(f"Uploaded job for {vm_name}: {len(test_ids)} tests, {len(test_ids) * args.iterations} total runs")
    
    # 5. Trigger VMs to start execution
    print(f"\nTriggering VMs...")
    worker_script = "resources/worker.sh"
    for vm_name in vms:
        vm_manager.run_worker_script(vm_name, args.zone, args.project, worker_script, args.benchmark_id, args.artifacts_bucket)
        print(f"  Started {vm_name}")
    
    # 6. Monitor progress by polling manifests
    print(f"\nMonitoring progress (polling every {args.poll_interval}s)...")
    completed = vm_manager.wait_for_completion(
        vms=vms,
        benchmark_id=args.benchmark_id,
        artifacts_bucket=args.artifacts_bucket,
        poll_interval=args.poll_interval,
        timeout=args.timeout
    )
    
    if not completed:
        print("\nERROR: Not all VMs completed successfully")
        sys.exit(1)
    
    print(f"\n✓ All VMs completed successfully!")
    
    # 7. Aggregate results
    print(f"\nAggregating results...")
    metrics = result_aggregator.aggregate_results(
        benchmark_id=args.benchmark_id,
        artifacts_bucket=args.artifacts_bucket,
        vms=vms
    )
    
    # 8. Generate report
    report_file = f"results/{args.benchmark_id}_report.txt"
    report_generator.generate_report(metrics, report_file)
    print(f"\nReport generated: {report_file}")
    
    print(f"\n========== Benchmark Complete ==========")


if __name__ == '__main__':
    main()
