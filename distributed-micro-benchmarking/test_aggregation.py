#!/usr/bin/env python3
"""Test script to run result aggregation independently"""

import sys
import subprocess
from helpers import result_aggregator, report_generator

def get_running_vms(instance_group, zone, project):
    """Get list of RUNNING VMs from instance group"""
    cmd = [
        'gcloud', 'compute', 'instance-groups', 'managed', 'list-instances',
        instance_group,
        f'--zone={zone}',
        f'--project={project}',
        '--filter=STATUS=RUNNING',
        '--format=value(NAME)'
    ]
    
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    vms = [vm.strip() for vm in result.stdout.strip().split('\n') if vm.strip()]
    return vms


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 test_aggregation.py <benchmark_id> <artifacts_bucket> [--instance-group <group> --zone <zone> --project <project>] [<vm1> <vm2> ...]")
        print("\nExample with instance group:")
        print("  python3 test_aggregation.py benchmark-1767457751 princer-working-dirs --instance-group princer-test --zone us-central1-a --project my-project")
        print("\nExample with VM list:")
        print("  python3 test_aggregation.py benchmark-1767457751 princer-working-dirs princer-test-m38x princer-test-x1r8")
        sys.exit(1)
    
    benchmark_id = sys.argv[1]
    artifacts_bucket = sys.argv[2]
    
    # Check if instance group is specified
    if '--instance-group' in sys.argv:
        ig_idx = sys.argv.index('--instance-group')
        zone_idx = sys.argv.index('--zone')
        project_idx = sys.argv.index('--project')
        
        instance_group = sys.argv[ig_idx + 1]
        zone = sys.argv[zone_idx + 1]
        project = sys.argv[project_idx + 1]
        
        print(f"Fetching running VMs from instance group: {instance_group}")
        vms = get_running_vms(instance_group, zone, project)
        
        if not vms:
            print("No running VMs found in instance group!")
            sys.exit(1)
    else:
        # Use provided VM list
        vms = sys.argv[3:]
        
        if not vms:
            print("Error: No VMs specified!")
            sys.exit(1)
    
    print(f"Testing aggregation for:")
    print(f"  Benchmark ID: {benchmark_id}")
    print(f"  Artifacts Bucket: {artifacts_bucket}")
    print(f"  VMs: {', '.join(vms)}")
    print()
    
    # Run aggregation
    print("Aggregating results...")
    metrics = result_aggregator.aggregate_results(benchmark_id, artifacts_bucket, vms)
    
    if not metrics:
        print("No metrics collected!")
        sys.exit(1)
    
    print(f"\nCollected metrics for {len(metrics)} tests:")
    for test_id, test_metrics in metrics.items():
        print(f"  Test {test_id}: {test_metrics}")
    
    # Generate report
    print("\nGenerating report...")
    report_file = f"results/{benchmark_id}_report.csv"
    report_generator.generate_report(metrics, report_file)
    
    print(f"\nReport saved to: {report_file}")

if __name__ == "__main__":
    main()
